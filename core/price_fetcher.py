"""
core/price_fetcher.py
- FinanceDataReader(FDR)로 국내 주식/ETF의 당일(가장 최근 영업일) 종가를 수집
- 수집 결과를 DB의 daily_prices 테이블에 UPSERT(INSERT ... ON DUPLICATE KEY UPDATE)
- transactions 원장에서 quantity > 0 으로 남아있는 6자리 숫자형 티커만 대상으로 함
  (해외주식/펀드/예금/현금 등은 다음 단계에서 별도 파이프라인으로 확장)
"""

import re
from datetime import datetime, timedelta

import FinanceDataReader as fdr

from database.connection import get_connection
from database.repository import AssetRepository

# ----------------------------------------------------------------------
# 1. 티커 분류 헬퍼
# ----------------------------------------------------------------------
_KR_TICKER_PATTERN = re.compile(r"^[0-9A-Z]{6}$")  # 국내 주식/ETF: 6자리 숫자


def is_kr_stock_ticker(ticker_code) -> bool:
    """국내 주식/ETF 여부 판별 (6자리 숫자)"""
    if not ticker_code:
        return False
    return bool(_KR_TICKER_PATTERN.match(str(ticker_code).strip()))


# ----------------------------------------------------------------------
# 2. FDR로 단일 종목 종가 수집
# ----------------------------------------------------------------------
def fetch_kr_stock_close(ticker_code: str):
    """
    FinanceDataReader를 이용해 특정 국내 종목의 가장 최근 영업일 종가를 수집.

    Returns:
        {"ticker_code": str, "price_date": date, "close_price": float} 또는 None.

    Notes:
        - 주말/장 시작 전이라도 FDR은 가장 최근 영업일의 row를 반환하므로
          별도의 영업일 보정 로직은 필요 없음.
        - 조회 기간을 최근 10일로 넉넉히 잡아 휴장일이 많아도 데이터를 확보.
    """
    code = str(ticker_code).strip()
    if not is_kr_stock_ticker(code):
        print(f"⚠️ 국내 주식/ETF 티커가 아닙니다 (code={code}) - 건너뜀")
        return None

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")

    try:
        df = fdr.DataReader(code, start_date, end_date)
    except Exception as e:
        print(f"❌ FDR 호출 실패 [{code}]: {e}")
        return None

    if df is None or df.empty:
        print(f"⚠️ FDR 데이터 없음 [{code}]")
        return None

    # 가장 최근 row = 가장 최근 영업일 (FDR은 영업일만 반환)
    latest = df.iloc[-1]
    price_date = df.index[-1].date()
    close_price = float(latest["Close"])

    return {
        "ticker_code": code,
        "price_date": price_date,
        "close_price": close_price,
    }


# ----------------------------------------------------------------------
# 3. daily_prices UPSERT
# ----------------------------------------------------------------------
def upsert_daily_price(ticker_code: str, price_date, close_price: float) -> bool:
    """
    daily_prices 테이블에 (price_date, ticker_code) PK 기준으로 UPSERT.
    MariaDB의 INSERT ... ON DUPLICATE KEY UPDATE 사용.
    """
    conn = get_connection()
    if not conn:
        return False

    # date/datetime 객체를 'YYYY-MM-DD' 문자열로 정규화
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


# ----------------------------------------------------------------------
# 4. 보유 종목 전체에 대한 오케스트레이터
# ----------------------------------------------------------------------
def collect_holdings_prices(verbose: bool = True) -> dict:
    """
    거래 원장에서 현재 잔고가 남아있는(quantity > 0) 종목을 조회한 뒤,
    그 중 국내 주식/ETF(6자리 숫자 티커)에 대해서만 종가를 수집하여
    daily_prices에 UPSERT.

    Returns:
        {
            "total_holdings": int,
            "kr_targets": int,
            "fetched": int,        # FDR 수집 성공
            "saved": int,          # DB UPSERT 성공
            "skipped": int,        # 티커 형식不符 등으로 스킵
            "failed": int,         # FDR 실패 또는 DB 저장 실패
            "results": [ {ticker_code, price_date, close_price, saved}, ... ]
        }
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()

    summary = {
        "total_holdings": len(holdings),
        "kr_targets": 0,
        "fetched": 0,
        "saved": 0,
        "skipped": 0,
        "failed": 0,
        "results": [],
    }

    # 동일 종목이 여러 계좌에 걸려있을 수 있어 중복 FDR 호출 방지
    seen_codes: set = set()

    for h in holdings:
        code = h.get("ticker_code")
        if not is_kr_stock_ticker(code):
            summary["skipped"] += 1
            if verbose:
                print(f"⏭️  스킵: {h.get('ticker_name')} ({code}) - 국내 주식/ETF 아님")
            continue

        if code in seen_codes:
            continue
        seen_codes.add(code)
        summary["kr_targets"] += 1

        if verbose:
            print(f"📈 수집 시도: {h.get('ticker_name')} ({code})")

        data = fetch_kr_stock_close(code)
        if not data:
            summary["failed"] += 1
            continue
        summary["fetched"] += 1

        saved = upsert_daily_price(
            ticker_code=data["ticker_code"],
            price_date=data["price_date"],
            close_price=data["close_price"],
        )

        result = {
            "ticker_code": data["ticker_code"],
            "price_date": data["price_date"],
            "close_price": data["close_price"],
            "saved": saved,
        }
        summary["results"].append(result)
        if saved:
            summary["saved"] += 1
            if verbose:
                print(
                    f"   ✅ 저장 완료: {data['ticker_code']} "
                    f"{data['price_date']} 종가 {data['close_price']:,.4f}"
                )
        else:
            summary["failed"] += 1

    if verbose:
        print("\n=== 일일 종가 수집 요약 ===")
        print(f"  전체 보유 종목: {summary['total_holdings']}")
        print(f"  국내 주식/ETF 대상: {summary['kr_targets']}")
        print(f"  FDR 수집 성공: {summary['fetched']}")
        print(f"  DB UPSERT 성공: {summary['saved']}")
        print(f"  스킵(해외/펀드/현금): {summary['skipped']}")
        print(f"  실패: {summary['failed']}")

    return summary


# ----------------------------------------------------------------------
# 5. CLI 진입점 (수동 실행 / 테스트용)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("🚀 일일 종가 수집 파이프라인 시작...")
    result = collect_holdings_prices(verbose=True)

    # 수집 직후 daily_prices 테이블 간단 조회
    conn = get_connection()
    if conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT price_date, ticker_code, close_price "
            "FROM daily_prices ORDER BY price_date DESC, ticker_code ASC"
        )
        rows = cur.fetchall()
        print(f"\n📦 daily_prices 현재 row 수: {len(rows)}")
        for row in rows[:30]:
            print(f"  - {row[0]} | {row[1]:8s} | {float(row[2]):,.4f}")
        cur.close()
        conn.close()
