"""
core/price_fetcher.py
- FinanceDataReader(FDR)로 국내 주식/ETF의 당일(가장 최근 영업일) 종가를 수집
- 해외주식(미국 주식 직투) USD 종가 + USD/KRW 기준환율 수집
- 수집 결과를 DB의 daily_prices 테이블에 UPSERT(INSERT ... ON DUPLICATE KEY UPDATE)
- transactions 원장에서 quantity > 0 으로 남아있는 종목을 대상으로 함
  - 국내 주식/ETF: 6자리 숫자 (기존)
  - 해외주식: 영문자만 1~5 자리 (AAPL, TSLA, QQQ, GOOGL, PFE, ...)
  - 환율: ticker_code='USD/KRW' (가상 식별자)
  - 펀드/예금/현금 등은 별도 파이프라인
"""

import re
from datetime import datetime, timedelta

import FinanceDataReader as fdr

from database.connection import get_connection
from database.repository import AssetRepository

# ----------------------------------------------------------------------
# 1. 티커 분류 헬퍼
# ----------------------------------------------------------------------
_KR_TICKER_PATTERN = re.compile(r"^[0-9A-Z]{6}$")  # 국내 주식/ETF: 6자리 숫자/영문 혼용
_US_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}$")  # 미국 주식: 영문 대문자 1~5자리
FX_TICKER = "USD/KRW"  # 원/달러 환율 가상 식별자


def is_kr_stock_ticker(ticker_code) -> bool:
    """국내 주식/ETF 여부 판별 (6자리 숫자/영문 혼용)

    예: 005930, 278530, 0181L0 (영숫자 혼용 ETF 단축코드)
    """
    if not ticker_code:
        return False
    return bool(_KR_TICKER_PATTERN.match(str(ticker_code).strip()))


def is_us_stock_ticker(ticker_code) -> bool:
    """미국 주식 여부 판별 (영문 대문자 1~5자리)

    예: AAPL, TSLA, QQQ, GOOGL, PFE
    """
    if not ticker_code:
        return False
    return bool(_US_TICKER_PATTERN.match(str(ticker_code).strip()))


def is_cash_asset(ticker_code) -> bool:
    """현금 자산 식별 (CASH_KRW / CASH_USD)."""
    if not ticker_code:
        return False
    code = str(ticker_code).strip().upper()
    return code in {"CASH_KRW", "CASH_USD", "KRW_CASH", "USD_CASH"}


_FUND_TICKER_PREFIXES = ("K55", "KR5")


def is_fund_ticker(ticker_code) -> bool:
    """IRP/연금 펀드 표준 코드 여부 판별.

    예: K55234BX0537 (KB투자 퇴직연금), KR5301AW7849 (미래에셋 퇴직연금)
    - ticker_code가 'K55' 또는 'KR5' 로 시작 + 길이 10+
    - 해외주식/현금/국내주식 패턴과도 겹치지 않음
    """
    if not ticker_code:
        return False
    code = str(ticker_code).strip().upper()
    return code.startswith(_FUND_TICKER_PREFIXES) and len(code) >= 10


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


def fetch_us_stock_close(ticker_code: str):
    """
    FinanceDataReader를 이용해 미국 주식의 가장 최근 영업일 USD 종가를 수집.

    Returns:
        {"ticker_code": str, "price_date": date, "close_price": float (USD)} 또는 None.

    Notes:
        - 미국 시장은 주말 + 미국 공휴일만 쉬므로 평일 오전에도 직전 영업일 데이터를 반환.
        - FDR 의 야후 파이낸스 백엔드 사용 (KRX 외 해외 소스).
        - 환율은 별도 fetch_usd_krw_rate() 로 수집.
    """
    code = str(ticker_code).strip().upper()
    if not is_us_stock_ticker(code):
        print(f"⚠️ 미국 주식 티커가 아닙니다 (code={code}) - 건너뜀")
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

    latest = df.iloc[-1]
    price_date = df.index[-1].date()
    close_price = float(latest["Close"])

    return {
        "ticker_code": code,
        "price_date": price_date,
        "close_price": close_price,
    }


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
    close_price = float(latest["Close"])  # KRW per USD

    return {
        "ticker_code": FX_TICKER,
        "price_date": price_date,
        "close_price": close_price,
    }


# ----------------------------------------------------------------------
# 2-b. 펀드(IRP/연금) 기준가(NAV) 수집기
# ----------------------------------------------------------------------
# 펀드 NAV 수집 데이터 소스 우선순위:
#   1) Override dict (테스트/수동 입력) — 가장 신뢰
#   2) 네이버 금융 펀드 페이지 스크래핑 — 현재 봇 차단으로 동작 불가 (TODO)
#   3) None 반환 (fallback)
#
# 환경변수 FUND_NAV_OVERRIDES 또는 환경에서 set_fund_nav_overrides() 호출로
# override 값을 주입할 수 있다. 예) {"K55234BX0537": 1025.45}
#
# 네이버 금융 페이지가 2026년 시점 기준 봇 차단 강화 상태라 requests 기반 직접
# 스크래핑이 어려움. 향후 KOFIA/한국신용데이터(KIND) 공식 API가 안정화되면
# fetch_fund_nav_from_naver() 부분을 대체 구현할 것.
_FUND_NAV_OVERRIDES: dict = {}


def set_fund_nav_overrides(overrides: dict) -> None:
    """수동 펀드 NAV override dict 를 설정 (테스트 / 운영자 입력용).

    형식: {ticker_code: {"nav": float, "price_date": "YYYY-MM-DD"}}
    """
    _FUND_NAV_OVERRIDES.clear()
    _FUND_NAV_OVERRIDES.update(overrides)


def get_fund_nav_overrides() -> dict:
    """현재 설정된 override dict 스냅샷 반환."""
    return dict(_FUND_NAV_OVERRIDES)


def fetch_fund_nav(ticker_code: str):
    """
    펀드 기준가(NAV) 수집.

    Args:
        ticker_code: 펀드 표준 코드 (예: 'K55234BX0537')

    Returns:
        {"ticker_code": str, "price_date": date, "close_price": float (NAV per 1000좌)}
        또는 None (수집 실패 시)

    백엔드 우선순위:
        1) _FUND_NAV_OVERRIDES dict
        2) 네이버 금융 펀드 페이지 (현재 미동작 — TODO)
    """
    code = str(ticker_code).strip().upper()
    if not is_fund_ticker(code):
        print(f"⚠️ 펀드 티커가 아닙니다 (code={code}) - 건너뜀")
        return None

    # --- 1) override 백엔드 ---
    if code in _FUND_NAV_OVERRIDES:
        entry = _FUND_NAV_OVERRIDES[code]
        nav = float(entry.get("nav", 0.0))
        # price_date 는 date/datetime/str 모두 허용
        pd = entry.get("price_date")
        if hasattr(pd, "date"):
            price_date = pd.date() if callable(pd.date) else pd
        elif isinstance(pd, str):
            price_date = datetime.strptime(pd[:10], "%Y-%m-%d").date()
        else:
            price_date = datetime.now().date()
        if nav <= 0:
            return None
        return {
            "ticker_code": code,
            "price_date": price_date,
            "close_price": nav,
        }

    # --- 2) 네이버 백엔드 (현재 비활성: 봇 차단) ---
    # TODO: KOFIA 펀드 검색 API 또는 KIND API가 안정화되면 이 부분을 활성화.
    # 현재는 네이버 금융 펀드 페이지가 User-Agent 차단/리다이렉트되어 동작하지 않음.
    # 환경변수 FUND_FETCH_ENABLED=1 일 때만 시도.
    import os

    if os.getenv("FUND_FETCH_ENABLED", "").strip() in {"1", "true", "yes"}:
        nav = _fetch_fund_nav_from_naver(code)
        if nav:
            return nav

    print(f"⚠️ 펀드 NAV 수집 실패 [{code}] — override 미설정 + 네이버 백엔드 비활성")
    return None


def _fetch_fund_nav_from_naver(ticker_code: str):
    """네이버 금융 펀드 페이지에서 NAV/기준일 추출 (베스트 노력).

    2026년 시점 네이버 금융 펀드 페이지 접근이 차단되어 사실상 동작하지 않음.
    향후 API 변경 시 selector 업데이트 필요.
    """
    # 의도적으로 requests + BeautifulSoup 구조만 남기고 실제 호출은 주석 처리.
    # import requests
    # from bs4 import BeautifulSoup
    # url = f"https://finance.naver.com/fund/fundInfo.naver?fundCd={ticker_code}"
    # resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 ..."}, timeout=5)
    # if resp.status_code != 200:
    #     return None
    # soup = BeautifulSoup(resp.text, "html.parser")
    # # NAV 셀렉터 (이전 버전 기준, 2026년경 변경됨) → selector 갱신 필요
    # nav_el = soup.select_one(...)
    # return {...}
    return None


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
    다음 자산군에 대해 종가/환율을 수집하여 daily_prices 에 UPSERT:

      1) 국내 주식/ETF (6자리 숫자 또는 영숫자 혼용 단축코드)
      2) 해외주식/미국 ETF (영문 1~5자리, 예: AAPL, QQQ)
      3) 환율 (USD/KRW — ticker_code='USD/KRW')

    펀드(K55..., KR...), 현금(CASH_KRW/CASH_USD), 정기예금 등은
    시세 수집 대상이 아니므로 스킵한다.

    Returns:
        {
            "total_holdings": int,
            "kr_targets": int,
            "us_targets": int,
            "fx_target": int,           # 환율 1건 고정
            "fetched": int,             # FDR 수집 성공 (3종목 모두 포함)
            "saved": int,               # DB UPSERT 성공
            "skipped": int,             # 티커 형식不符 등으로 스킵
            "failed": int,
            "results": [ {ticker_code, price_date, close_price, saved, asset_class}, ... ]
        }
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()

    summary = {
        "total_holdings": len(holdings),
        "kr_targets": 0,
        "us_targets": 0,
        "fund_targets": 0,
        "fx_target": 1,  # 환율은 1건
        "fetched": 0,
        "saved": 0,
        "skipped": 0,
        "failed": 0,
        "results": [],
    }

    seen_codes: set = set()

    for h in holdings:
        code = h.get("ticker_code")
        name = h.get("ticker_name")

        if is_kr_stock_ticker(code):
            asset_class = "kr"
            fetcher = fetch_kr_stock_close
            summary["kr_targets"] += 1
        elif is_us_stock_ticker(code):
            asset_class = "us"
            fetcher = fetch_us_stock_close
            summary["us_targets"] += 1
        elif is_fund_ticker(code):
            asset_class = "fund"
            fetcher = fetch_fund_nav
            summary["fund_targets"] += 1
        else:
            summary["skipped"] += 1
            if verbose:
                print(f"⏭️  스킵: {name} ({code}) - 시세 수집 대상 아님 (현금/예금)")
            continue

        if code in seen_codes:
            continue
        seen_codes.add(code)

        if verbose:
            print(f"📈 수집 시도: {name} ({code})  [{asset_class.upper()}]")

        data = fetcher(code)
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
            "asset_class": asset_class,
        }
        summary["results"].append(result)
        if saved:
            summary["saved"] += 1
            if verbose:
                unit = "$" if asset_class == "us" else ""
                print(
                    f"   ✅ 저장 완료: {data['ticker_code']} "
                    f"{data['price_date']} 종가 {unit}{data['close_price']:,.4f}"
                )
        else:
            summary["failed"] += 1

    # 환율 수집 (1회)
    if verbose:
        print("💱 환율 수집 시도: USD/KRW")
    fx_data = fetch_usd_krw_rate()
    if fx_data:
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
    else:
        summary["failed"] += 1

    if verbose:
        print("\n=== 일일 종가/환율 수집 요약 ===")
        print(f"  전체 보유 종목: {summary['total_holdings']}")
        print(f"  국내 주식/ETF 대상: {summary['kr_targets']}")
        print(f"  해외주식 대상: {summary['us_targets']}")
        print(f"  환율 대상: {summary['fx_target']}")
        print(f"  FDR 수집 성공: {summary['fetched']}")
        print(f"  DB UPSERT 성공: {summary['saved']}")
        print(f"  스킵(펀드/현금/예금): {summary['skipped']}")
        print(f"  실패: {summary['failed']}")

    return summary


# ----------------------------------------------------------------------
# 5. CLI 진입점 (수동 실행 / 테스트용)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    print("🚀 일일 종가/환율 수집 파이프라인 시작...")
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
        for row in rows[:40]:
            code = row[1]
            mark = "💱" if code == "USD/KRW" else "📈"
            print(f"  - {row[0]} | {mark} {code:10s} | {float(row[2]):,.4f}")
        cur.close()
        conn.close()

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
