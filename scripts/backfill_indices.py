import os
import sys
from datetime import datetime, timedelta

# 프로젝트 루트 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import FinanceDataReader as fdr

from core.fetcher.fx import BENCHMARK_SOURCE_SYMBOLS
from database.connection import execute_many


def backfill_benchmarks():
    """
    주요 지수 및 환율의 과거 1년치 데이터를 일괄 적재
    """
    tickers = ["KS11", "KQ11", "US500", "IXIC", "DJI", "USD/KRW"]
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)

    print(f"🚀 백필 시작: {start_date.date()} ~ {end_date.date()}")

    for ticker in tickers:
        print(f"📝 수집 중: {ticker}")
        try:
            source_symbol = BENCHMARK_SOURCE_SYMBOLS.get(ticker, ticker)
            df = fdr.DataReader(source_symbol, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))

            # 결측치 제거
            df = df.dropna(subset=["Close"])
            df = df[df["Close"] > 0]

            if df.empty:
                print(f"⚠️ {ticker} 데이터 없음")
                continue

            records = [(date.date(), ticker, float(row["Close"])) for date, row in df.iterrows()]
            saved = execute_many(
                """
                INSERT INTO daily_prices (price_date, ticker_code, close_price)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE close_price = VALUES(close_price)
            """,
                records,
            )
            if not saved:
                print("❌ DB 연결 실패")
                return
            print(f"   ✅ {ticker} 적재 완료 ({len(df)}건)")
        except Exception as e:
            print(f"❌ {ticker} 에러: {e}")

    print("🚀 백필 종료.")


if __name__ == "__main__":
    backfill_benchmarks()
