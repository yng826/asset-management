"""
core/fetcher/fx.py
- USD/KRW 환율 및 벤치마크 지수 수집 모듈
"""

from datetime import datetime, timedelta

import FinanceDataReader as fdr

from core.fetcher.kr_stock import upsert_daily_price
from database.connection import get_connection

FX_TICKER = "USD/KRW"


def is_cash_asset(ticker_code) -> bool:
    """현금 자산 식별 (CASH_KRW / CASH_USD)."""
    if not ticker_code:
        return False
    code = str(ticker_code).strip().upper()
    return code in {"CASH_KRW", "CASH_USD", "KRW_CASH", "USD_CASH"}


def fetch_usd_krw_rate():
    """
    FinanceDataReader를 이용해 USD/KRW (원/달러) 기준환율의
    가장 최근 영업일 종가를 수집.

    Returns:
        {"ticker_code": "USD/KRW", "price_date": date, "close_price": float (KRW per USD)} 또는 None.
    """
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")

    try:
        df = fdr.DataReader("USD/KRW", start_date, end_date)
    except Exception as e:
        print(f"❌ FDR 환율 호출 실패 [USD/KRW]: {e}")
        return None

    if df is None or df.empty:
        print("⚠️ FDR 환율 데이터 없음")
        return None

    latest = df.iloc[-1]
    price_date = df.index[-1].date()
    close_price = float(latest["Close"])

    return {
        "ticker_code": FX_TICKER,
        "price_date": price_date,
        "close_price": close_price,
    }


def collect_fx_rate(verbose: bool = True) -> dict:
    """
    USD/KRW 환율을 수집하여 DB에 저장.
    """
    summary = {
        "fx_target": 1,
        "fetched": 0,
        "saved": 0,
        "failed": 0,
        "results": [],
    }

    if verbose:
        print("💱 환율 수집 시도: USD/KRW")

    fx_data = fetch_usd_krw_rate()
    if not fx_data:
        summary["failed"] += 1
        return summary

    summary["fetched"] += 1
    saved_fx = upsert_daily_price(
        ticker_code=fx_data["ticker_code"],
        price_date=fx_data["price_date"],
        close_price=fx_data["close_price"],
    )
    result = {
        "ticker_code": fx_data["ticker_code"],
        "price_date": fx_data["price_date"],
        "close_price": fx_data["close_price"],
        "saved": saved_fx,
        "asset_class": "fx",
    }
    summary["results"].append(result)
    if saved_fx:
        summary["saved"] += 1
        if verbose:
            print(
                f"   ✅ 저장 완료: {fx_data['ticker_code']} "
                f"{fx_data['price_date']} ₩{fx_data['close_price']:,.2f}/USD"
            )
    else:
        summary["failed"] += 1

    return summary


def fetch_and_save_benchmarks():
    """
    주요 벤치마크 지수(KOSPI, KOSDAQ, S&P500, Nasdaq, DJI) 및 환율 수집 파이프라인
    - 매일 종가 수집
    """
    tickers = {
        "KS11": "KOSPI",
        "KQ11": "KOSDAQ",
        "US500": "S&P500",
        "IXIC": "NASDAQ",
        "DJI": "DJI",
        "USD/KRW": "EXCHANGE_RATE",
    }

    end_date = datetime.now()
    start_date = end_date - timedelta(days=5)

    conn = get_connection()
    if not conn:
        print("❌ DB 연결 실패로 벤치마크 수집 불가")
        return

    cur = conn.cursor()

    print("📊 벤치마크 지수 및 환율 수집 시작...")

    for ticker_code, name in tickers.items():
        try:
            df = fdr.DataReader(ticker_code, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
            df = df.dropna(subset=["Close"])
            df = df[df["Close"] > 0]

            if df.empty:
                print(f"⚠️ {name}({ticker_code}) 데이터 없음")
                continue

            latest = df.iloc[-1]
            price_date = df.index[-1].date()
            close_price = float(latest["Close"])

            cur.execute(
                """
                INSERT INTO daily_prices (price_date, ticker_code, close_price)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE close_price = VALUES(close_price)
            """,
                (price_date, ticker_code, close_price),
            )

            print(f"   ✅ 저장 완료: {name}({ticker_code}) {price_date} {close_price}")
        except Exception as e:
            print(f"❌ {name}({ticker_code}) 수집/저장 실패: {e}")

    conn.commit()
    cur.close()
    conn.close()
