"""
과거 시세 일괄 수집 및 daily_prices 백필 스크립트
- 주식/ETF: 2026-05-01 ~ 어제 (FDR)
- 가상자산: 2025-01-01 ~ 어제 (Upbit 페이징 수집)
- 저장소: daily_prices 테이블 (UPSERT)
"""

import sys
import time
from datetime import datetime, timedelta
import FinanceDataReader as fdr
import pandas as pd
import pyupbit
import re

try:
    from database.connection import get_connection
except ImportError:
    print("❌ 프로젝트 루트에서 실행하거나 PYTHONPATH를 설정하세요.")
    sys.exit(1)

# 기간 설정
STOCK_START_DATE = "2026-05-01"
CRYPTO_START_DATE = "2025-01-01"
END_DATE = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def get_target_tickers() -> tuple[list[str], list[str]]:
    """transactions 테이블에서 고유 ticker_code 추출 (현금/펀드 제외, 정규식 필터링)"""
    conn = get_connection()
    if not conn:
        print("❌ DB 연결 실패")
        sys.exit(1)

    stocks = []
    cryptos = []

    # 국내 주식(6자리 숫자) 또는 미국 티커(1~5자리 영문 대문자)
    stock_pattern = re.compile(r"^([0-9]{6}|[A-Z]{1,5})$")

    try:
        cur = conn.cursor()
        query = """
            SELECT DISTINCT ticker_code 
            FROM transactions 
            WHERE ticker_code IS NOT NULL AND ticker_code != ''
        """
        cur.execute(query)
        rows = cur.fetchall()

        for (code,) in rows:
            code = code.strip().upper()

            # 1. 현금 및 펀드 코드(ISIN: K5, KR5 등) 제외
            if code.startswith(("CASH", "KR5", "K5")) or code in ("KRW", "USD"):
                continue

            # 2. 업비트 가상자산
            if code.startswith(("KRW-", "BTC-", "USDT-")):
                cryptos.append(code)
            # 3. 유효한 주식/ETF만 추가
            elif stock_pattern.match(code):
                stocks.append(code)
            else:
                print(f"ℹ️ 시세 수집 대상 제외 (규격 외 코드): {code}")

    finally:
        cur.close()
        conn.close()

    return sorted(stocks), sorted(cryptos)


def backfill_stocks(cur, stocks: list[str]) -> int:
    """국내/해외 주식/ETF 과거 일봉 수집 (2026-05-01 ~ 어제)"""
    total_inserted = 0
    # MariaDB Connector는 ? 파라미터 마커를 표준 지원합니다.
    query = """
        INSERT INTO daily_prices (price_date, ticker_code, close_price)
        VALUES (?, ?, ?)
        ON DUPLICATE KEY UPDATE close_price = VALUES(close_price)
    """

    print(f"\n📈 [주식/ETF] 총 {len(stocks)}개 종목 시세 수집 시작 ({STOCK_START_DATE} ~ {END_DATE})...")
    for code in stocks:
        try:
            df = fdr.DataReader(code, STOCK_START_DATE, END_DATE)
            if df.empty or "Close" not in df.columns:
                print(f"  ⚠️ {code}: 시세 데이터 없음 (스킵)")
                continue

            records = []
            for date_idx, row in df.iterrows():
                p_date = date_idx.strftime("%Y-%m-%d")
                price = float(row["Close"])
                records.append((p_date, code, price))

            cur.executemany(query, records)
            total_inserted += len(records)
            print(f"  ✅ {code}: {len(records)}일치 시세 적재 완료")

        except Exception as e:
            print(f"  ❌ {code} 수집 중 오류: {e}")

    return total_inserted


def fetch_upbit_candles_paging(ticker: str, start_date: str, end_date: str) -> list[tuple[str, str, float]]:
    """업비트 200개 제한 극복을 위한 과거 일봉 페이징 수집"""
    records = []
    # 어제 23:59:59 기준부터 과거로 거슬러 올라감
    curr_to = f"{end_date} 23:59:59"
    target_start_dt = datetime.strptime(start_date, "%Y-%m-%d")

    while True:
        # 1회 최대 200개 조회
        df = pyupbit.get_ohlcv(ticker, interval="day", to=curr_to, count=200)
        if df is None or df.empty:
            break

        # KST 타임존 제거
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        chunk_records = []
        reached_start = False

        # 최신순 정렬을 위해 내림차순 순회
        for date_idx in reversed(df.index):
            p_date = date_idx.strftime("%Y-%m-%d")
            
            if date_idx < target_start_dt:
                reached_start = True
                break

            if p_date <= end_date:
                price = float(df.loc[date_idx, "close"])
                chunk_records.append((p_date, ticker, price))

        records.extend(chunk_records)

        # 시작일에 도달했거나 더 이상 이전 데이터가 없으면 중단
        if reached_start or len(df) < 200:
            break

        # 가장 과거 날짜를 다음 조회 기준으로 설정 (1초 전으로 설정하여 중복 방지)
        oldest_dt = df.index[0]
        curr_to = (oldest_dt - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        
        # Upbit API Rate Limit 준수 (초당 10회 제한 방지)
        time.sleep(0.12)

    # 중복 제거 (날짜 기준)
    dedup = {r[0]: r for r in records}
    return sorted(dedup.values(), key=lambda x: x[0])


def backfill_cryptos(cur, cryptos: list[str]) -> int:
    """가상자산(업비트) 2025-01-01부터 과거 일봉 수집"""
    total_inserted = 0
    query = """
        INSERT INTO daily_prices (price_date, ticker_code, close_price)
        VALUES (?, ?, ?)
        ON DUPLICATE KEY UPDATE close_price = VALUES(close_price)
    """

    print(f"\n🪙 [가상자산] 총 {len(cryptos)}개 코인 시세 수집 시작 ({CRYPTO_START_DATE} ~ {END_DATE})...")
    for ticker in cryptos:
        try:
            records = fetch_upbit_candles_paging(ticker, CRYPTO_START_DATE, END_DATE)
            if not records:
                print(f"  ⚠️ {ticker}: 시세 데이터 없음 (스킵)")
                continue

            cur.executemany(query, records)
            total_inserted += len(records)
            print(f"  ✅ {ticker}: {len(records)}일치 시세 적재 완료 ({records[0][0]} ~ {records[-1][0]})")

        except Exception as e:
            print(f"  ❌ {ticker} 수집 중 오류: {e}")

    return total_inserted


def main():
    print(f"🚀 daily_prices 백필 시작 (주식: {STOCK_START_DATE}~ / 코인: {CRYPTO_START_DATE}~) -> 종료일: {END_DATE}")

    stocks, cryptos = get_target_tickers()
    print(f"🎯 대상 확인 -> 주식: {len(stocks)}개, 코인: {len(cryptos)}개")

    conn = get_connection()
    if not conn:
        print("❌ DB 연결 실패")
        return

    try:
        cur = conn.cursor()
        s_count = backfill_stocks(cur, stocks)
        c_count = backfill_cryptos(cur, cryptos)
        conn.commit()

        print("\n" + "=" * 55)
        print("🎉 daily_prices 시세 백필 성공!")
        print(f"- 주식/ETF 적재 건수: {s_count}건")
        print(f"- 가상자산 적재 건수: {c_count}건 (2025-01-01부터 약 600+일치)")
        print(f"- 총 적재 데이터 건수: {s_count + c_count}건")
        print("=" * 55)

    except Exception as e:
        conn.rollback()
        print(f"❌ DB 작업 중 오류 발생 (Rollback): {e}")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()