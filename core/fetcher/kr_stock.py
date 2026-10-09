"""
core/fetcher/kr_stock.py
- 국내 주식/ETF FDR 종가 수집 모듈
"""

import re
from datetime import datetime, timedelta

import FinanceDataReader as fdr

from database.connection import get_connection
from database.repository import AssetRepository

_KR_TICKER_PATTERN = re.compile(r"^[0-9A-Z]{6}$")  # 국내 주식/ETF: 6자리 숫자/영문 혼용

# 수집 시 덮어쓸 최근 영업일 수: 16:00 수집값이 확정 종가와 다를 수 있어 다음 수집 때 재기록
KR_REFRESH_DAYS = 5


def is_kr_stock_ticker(ticker_code) -> bool:
    """국내 주식/ETF 여부 판별 (6자리 숫자/영문 혼용)

    예: 005930, 278530, 0181L0 (영숫자 혼용 ETF 단축코드)
    """
    if not ticker_code:
        return False
    return bool(_KR_TICKER_PATTERN.match(str(ticker_code).strip()))


def fetch_kr_stock_close(ticker_code: str):
    """
    FinanceDataReader를 이용해 특정 국내 종목의 가장 최근 영업일 종가를 수집.

    Returns:
        {"ticker_code": str, "price_date": date, "close_price": float} 또는 None.
    """
    closes = fetch_kr_stock_closes(ticker_code, days=1)
    if not closes:
        return None
    return {"ticker_code": str(ticker_code).strip(), **closes[-1]}


def fetch_kr_stock_closes(ticker_code: str, days: int = KR_REFRESH_DAYS) -> list[dict]:
    """
    FinanceDataReader를 이용해 특정 국내 종목의 최근 days 영업일 종가 목록을 수집 (오래된 순).

    Returns:
        [{"price_date": date, "close_price": float}, ...] (데이터 없으면 빈 리스트)
    """
    code = str(ticker_code).strip()
    if not is_kr_stock_ticker(code):
        print(f"⚠️ 국내 주식/ETF 티커가 아닙니다 (code={code}) - 건너뜀")
        return []

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")

    try:
        df = fdr.DataReader(code, start_date, end_date)
    except Exception as e:
        print(f"❌ FDR 호출 실패 [{code}]: {e}")
        return []

    if df is None or df.empty:
        print(f"⚠️ FDR 데이터 없음 [{code}]")
        return []

    df = df.dropna(subset=["Close"]).tail(days)
    return [{"price_date": idx.date(), "close_price": float(row["Close"])} for idx, row in df.iterrows()]


def upsert_daily_price(ticker_code: str, price_date, close_price: float) -> bool:
    """
    daily_prices 테이블에 (price_date, ticker_code) PK 기준으로 UPSERT.
    """
    conn = get_connection()
    if not conn:
        return False

    if hasattr(price_date, "strftime"):
        price_date_str = price_date.strftime("%Y-%m-%d")
    else:
        price_date_str = str(price_date)[:10]

    query = """
        INSERT INTO daily_prices (price_date, ticker_code, close_price)
        VALUES (?, ?, ?)
        ON DUPLICATE KEY UPDATE
            close_price = VALUES(close_price),
            updated_at = CURRENT_TIMESTAMP
    """
    try:
        cur = conn.cursor()
        cur.execute(query, (price_date_str, ticker_code, close_price))
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ daily_prices UPSERT 실패 [{ticker_code}@{price_date_str}]: {e}")
        conn.close()
        return False


def collect_kr_prices(verbose: bool = True) -> dict:
    """
    보유 종목 중 국내 주식/ETF 종가를 수집하여 DB에 저장.
    - 최근 KR_REFRESH_DAYS 영업일 종가를 모두 UPSERT (직전 수집의 미확정 종가를 확정값으로 덮어씀)
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()

    summary = {
        "kr_targets": 0,
        "fetched": 0,
        "saved": 0,
        "failed": 0,
        "results": [],
    }

    seen_codes: set = set()

    for h in holdings:
        code = h.get("ticker_code")
        name = h.get("ticker_name")

        if not is_kr_stock_ticker(code):
            continue

        if code in seen_codes:
            continue
        seen_codes.add(code)

        summary["kr_targets"] += 1
        if verbose:
            print(f"📈 국내 주식 수집 시도: {name} ({code})")

        closes = fetch_kr_stock_closes(code)
        if not closes:
            summary["failed"] += 1
            continue

        summary["fetched"] += 1
        saved = True
        for close in closes:
            saved = upsert_daily_price(code, close["price_date"], close["close_price"]) and saved
        data = {"ticker_code": code, **closes[-1]}
        result = {
            "ticker_code": data["ticker_code"],
            "price_date": data["price_date"],
            "close_price": data["close_price"],
            "saved": saved,
            "asset_class": "kr",
        }
        summary["results"].append(result)
        if saved:
            summary["saved"] += 1
            if verbose:
                print(f"   ✅ 저장 완료: {code} {data['price_date']} ₩{data['close_price']:,.2f}")
        else:
            summary["failed"] += 1

    return summary
