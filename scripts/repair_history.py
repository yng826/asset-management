"""과거 누락 일자 통합 복구 스크립트: 시세 백필 + 스냅샷 재계산 사용법:

python3 -m scripts.repair_history 2026-09-08 2026-09-10
"""

import sys
from datetime import datetime, timedelta

import FinanceDataReader as fdr
import pyupbit
from scripts.backfill_snapshots import backfill_snapshots

from database.connection import get_connection
from database.repository import AssetRepository


def backfill_missing_prices(start_date: str, end_date: str) -> None:
    """특정 기간 동안 보유 종목의 주식 및 가상자산 일봉을 daily_prices에 UPSERT"""
    repo = AssetRepository()
    holdings = repo.get_current_holdings()
    conn = get_connection()
    if not conn:
        print("❌ DB 연결 실패")
        return

    cur = conn.cursor()
    upsert_sql = """
        INSERT INTO daily_prices (price_date, ticker_code, close_price)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE close_price = VALUES(close_price)
    """

    print(f"📥 {start_date} ~ {end_date} 시세 복구 수집 시작...")
    s_dt = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")

    for h in holdings:
        code = h["ticker_code"]
        if "CASH" in code or code.startswith(("KR5", "K5")):
            continue

        try:
            if code.startswith("KRW-"):
                # 가상자산 (업비트)
                df = pyupbit.get_ohlcv(code, interval="day", to=f"{end_date} 23:59:59", count=30)
                if df is not None and not df.empty:
                    df.index = df.index.tz_localize(None)
                    records = [
                        (idx.strftime("%Y-%m-%d"), code, float(row["close"]))
                        for idx, row in df.iterrows()
                        if start_date <= idx.strftime("%Y-%m-%d") <= end_date
                    ]
                    if records:
                        cur.executemany(upsert_sql, records)
            else:
                # 국내 / 해외 주식
                df = fdr.DataReader(code, s_dt, end_date)
                if not df.empty and "Close" in df.columns:
                    records = [
                        (idx.strftime("%Y-%m-%d"), code, float(row["Close"]))
                        for idx, row in df.iterrows()
                        if start_date <= idx.strftime("%Y-%m-%d") <= end_date
                    ]
                    if records:
                        cur.executemany(upsert_sql, records)
        except Exception as e:
            print(f"⚠️ {code} 시세 수집 실패: {e}")

    conn.commit()
    cur.close()
    conn.close()
    print("✅ 시세 복구 및 daily_prices 적재 완료")


def repair(start_date: str, end_date: str) -> None:
    # 1. 누락 시세 수집 및 DB UPSERT
    backfill_missing_prices(start_date, end_date)

    # 2. 채워진 시세를 바탕으로 일일 총합 및 세부 종목 스냅샷 일괄 계산
    backfill_snapshots(start_date, end_date)
    print(f"🎉 {start_date} ~ {end_date} 데이터 무결성 복구 완료!")


if __name__ == "__main__":
    s = sys.argv[1] if len(sys.argv) > 1 else "2026-09-08"
    e = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y-%m-%d")
    repair(s, e)
