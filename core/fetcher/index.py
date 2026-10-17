"""
core/fetcher/index.py
- 국가·해외 기초지수 일봉 수집 모듈 (k3s 수집기 `indices` 작업 전용, 봇 APScheduler 는 호출하지 않음)

- 저장 코드는 `IDX:` 접두어로 종목 코드와 구분 (기존 벤치마크 KS11·US500 등은 fx.py 가 그대로 관리)
- 값은 각 지수의 원래 포인트 (통화 환산 없음)
- 최근 N일을 전부 UPSERT 하므로 하루 빠져도 다음 실행에서 메워지고, 큰 N 으로 1회 실행하면 백필이 된다
"""

from datetime import datetime, timedelta

import FinanceDataReader as fdr

from config.settings import PRICE_TABLE
from database.connection import execute_many

INDEX_PREFIX = "IDX:"

# 저장 코드: (이름, 지역, FDR 소스 심볼)
INDICES = {
    "IDX:KS200": ("KOSPI 200", "한국", "YAHOO:^KS200"),
    "IDX:NDX": ("나스닥 100", "미국", "YAHOO:^NDX"),
    "IDX:N225": ("닛케이 225", "일본", "YAHOO:^N225"),
    "IDX:SSEC": ("상해종합", "중국", "YAHOO:000001.SS"),
    "IDX:HSI": ("항셍", "홍콩", "YAHOO:^HSI"),
    "IDX:TWII": ("가권(TAIEX)", "대만", "YAHOO:^TWII"),
    "IDX:NSEI": ("Nifty 50", "인도", "YAHOO:^NSEI"),
    "IDX:STOXX50E": ("유로스톡스 50", "유럽", "YAHOO:^STOXX50E"),
    "IDX:GDAXI": ("DAX", "독일", "YAHOO:^GDAXI"),
    "IDX:FTSE": ("FTSE 100", "영국", "YAHOO:^FTSE"),
}


def is_index_ticker(ticker_code) -> bool:
    """수집기 관리 지수 코드 식별 (IDX: 접두어)."""
    return bool(ticker_code) and str(ticker_code).upper().startswith(INDEX_PREFIX)


def collect_index_prices(days: int = 7) -> dict:
    """
    INDICES 의 최근 days 일 일봉 종가를 PRICE_TABLE 에 UPSERT.

    Returns:
        {"targets": int, "saved_rows": int, "failed": [코드, ...]}
    """
    # Yahoo 소스는 종료일 미포함 → 당일 종가까지 받도록 +1일
    end_date = datetime.now() + timedelta(days=1)
    start_date = end_date - timedelta(days=days + 1)
    summary = {"targets": len(INDICES), "saved_rows": 0, "failed": []}

    codes = list(INDICES)
    for i, ticker_code in enumerate(codes):
        name, region, source_symbol = INDICES[ticker_code]
        try:
            df = fdr.DataReader(source_symbol, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
            df = df.dropna(subset=["Close"])
            df = df[df["Close"] > 0]
            if df.empty:
                print(f"⚠️ {region} {name}({ticker_code}) 데이터 없음")
                summary["failed"].append(ticker_code)
                continue

            rows = [(ts.date(), ticker_code, float(close)) for ts, close in df["Close"].items()]
            saved = execute_many(
                f"""
                INSERT INTO {PRICE_TABLE} (price_date, ticker_code, close_price)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE close_price = VALUES(close_price)
            """,
                rows,
            )
            if not saved:
                print("❌ DB 연결 실패로 지수 수집 중단")
                summary["failed"].extend(codes[i:])
                return summary

            summary["saved_rows"] += len(rows)
            print(f"   ✅ {region} {name}({ticker_code}) {len(rows)}건, 최신 {rows[-1][0]} {rows[-1][2]}")
        except Exception as e:
            print(f"❌ {region} {name}({ticker_code}) 수집/저장 실패: {e}")
            summary["failed"].append(ticker_code)

    return summary
