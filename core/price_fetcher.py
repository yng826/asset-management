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
import requests

from config.settings import DATA_GO_KR_API_KEY
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
        1) 공공데이터포털 금융위 Open API (_fetch_fund_nav_from_data_go_kr)
           → 2026-09 시점 표준코드 매핑 + basDt 까지만 반환 (NAV 자체는 미제공)
        2) _FUND_NAV_OVERRIDES dict (운영자 수동 주입)
        3) None 반환 → fallback (price_source='fallback' 으로 avg_price 처리)
    """
    code = str(ticker_code).strip().upper()
    if not is_fund_ticker(code):
        print(f"⚠️ 펀드 티커가 아닙니다 (code={code}) - 건너뜀")
        return None

    # --- 1) 공공데이터포털 백엔드 (정상 동작하지만 NAV는 미제공) ---
    import os

    if os.getenv("DATA_GO_KR_FUND_FETCH_ENABLED", "1").strip() in {"1", "true", "yes"}:
        nav = _fetch_fund_nav_from_data_go_kr(code)
        if nav:
            return nav

    # --- 2) override 백엔드 (운영자 수동 주입) ---
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

    print(f"⚠️ 펀드 NAV 수집 실패 [{code}] — 데이터포털 미제공 + override 미설정")
    return None


def _fetch_fund_nav_from_naver(ticker_code: str):
    """[DEPRECATED] 네이버 금융 펀드 페이지 직접 스크래핑 — 폐기됨.

    2026년 시점 네이버 금융 펀드 페이지가 봇 차단/리다이렉트되어 동작 불가.
    - 외부 스크래퍼 의존 제거 → 공공데이터포털(_fetch_fund_nav_from_data_go_kr)로 이전.
    - 본 함수는 하위 호환을 위해 stub 으로 남겨두고 항상 None 반환.
    """
    return None


# ----------------------------------------------------------------------
# 2-c. 공공데이터포털(금융위원회 증권정보 Open API) 펀드 수집기
# ----------------------------------------------------------------------
# 엔드포인트:
#   GET https://apis.data.go.kr/1160100/service/GetFundProductInfoService/getStandardCodeInfo
#   - params: serviceKey (인코딩된 인증키), resultType=json, numOfRows, srtnCd
#   - 응답 items.item[].asoStdCd (펀드표준코드), basDt (기준일자), srtnCd, fndNm 등
#
# 국내 펀드 표준코드 패턴 (사용자 검증 완료):
#   K55 + 운용사코드(3) + srtnCd(5) + 체크디지트(1) = 12자리
#   KR5 + 운용사코드(3) + srtnCd(5) + 체크디지트(1) = 12자리
#   예) K55234BX0537 → srtnCd='BX053', asoStdCd='K55234BX0537'
#   예) KR5301AW7849 → srtnCd='AW784', asoStdCd='KR5301AW7849'
#
# NOTE: 2026-09 시점 본 API는 srtnCd → asoStdCd(표준코드) 매핑과 basDt(기준일자)까지만
#       반환하며, NAV(기준가격) 자체는 반환하지 않는다. 향후 펀드 NAV 조회
#       엔드포인트가 추가되면 _fetch_fund_nav_from_data_go_kr() 본체를 확장.
DATA_GO_KR_BASE_URL = "https://apis.data.go.kr/1160100/service/GetFundProductInfoService/getStandardCodeInfo"


def extract_srtn_cd(aso_std_cd: str) -> str | None:
    """펀드 표준코드(asoStdCd)에서 단축코드(srtnCd) 5자리를 추출.

    국내 펀드 표준코드 패턴:
      K55 + 운용사코드(3) + srtnCd(5) + 체크디지트(1) = 12자리
      KR5 + 운용사코드(3) + srtnCd(5) + 체크디지트(1) = 12자리

    예)
      K55234BX0537 → 'BX053'
      KR5301AW7849 → 'AW784'

    Returns:
        srtnCd 5자리 또는 None (패턴 불일치 시)
    """
    if not aso_std_cd:
        return None
    code = str(aso_std_cd).strip().upper()
    # K55... 또는 KR5... 시작 + 길이 12
    if not (code.startswith(("K55", "KR5")) and len(code) >= 10):
        return None
    # 단축코드는 7번째 글자부터 5자리 (0-indexed 6~11)
    # K55(3) + 운용사(3) + srtnCd(5) + 체크(1) = 12
    #            ^6        ^6  ^10
    # 만약 길이 12 → 정확히 6~10 슬라이스
    if len(code) >= 11:
        return code[6:11]
    # 만약 길이 10~11 (체크디지트 생략 등 변형) → 끝 5자리
    if len(code) >= 10:
        return code[-5:]
    return None


def _data_go_kr_get(params: dict, timeout: int = 8):
    """공공데이터포털 GET 호출 헬퍼.

    - requests 가 serviceKey 를 자동 URL 인코딩 (POSTMAN 등 외부에서
      %3D%3D 로 인코딩된 키와 동등 처리)
    - timeout / 네트워크 오류는 None 반환
    """
    if not DATA_GO_KR_API_KEY:
        print("⚠️ DATA_GO_KR_API_KEY 미설정 (config.settings / .env 확인)")
        return None
    merged = {"serviceKey": DATA_GO_KR_API_KEY, "resultType": "json", **params}
    try:
        resp = requests.get(DATA_GO_KR_BASE_URL, params=merged, timeout=timeout)
    except Exception as e:
        print(f"❌ 공공데이터포털 호출 실패: {e}")
        return None
    if resp.status_code != 200:
        print(f"⚠️ 공공데이터포털 HTTP {resp.status_code}: {resp.text[:200]}")
        return None
    try:
        return resp.json()
    except Exception as e:
        print(f"❌ 공공데이터포털 JSON 파싱 실패: {e}")
        return None


def _fetch_aso_std_cd_from_data_go_kr(srtn_cd: str) -> dict | None:
    """공공데이터포털 getStandardCodeInfo 호출로 srtnCd → asoStdCd 매핑 조회.

    Returns:
        {"asoStdCd": str, "basDt": "YYYYMMDD", "srtnCd": str, "fndNm": str}
        또는 None (실패 시)
    """
    data = _data_go_kr_get({"numOfRows": 5, "srtnCd": srtn_cd})
    if not data:
        return None
    # 응답 구조: response.body.items.item[]
    try:
        body = data.get("response", {}).get("body", {})
        items = body.get("items", {}).get("item", [])
        if not items:
            print(f"⚠️ 공공데이터포털 items 비어있음 (srtnCd={srtn_cd})")
            return None
        item = items[0]
        return {
            "asoStdCd": item.get("asoStdCd"),
            "basDt": item.get("basDt"),  # 'YYYYMMDD' 형식
            "srtnCd": item.get("srtnCd"),
            "fndNm": item.get("fndNm"),
        }
    except (AttributeError, TypeError, IndexError) as e:
        print(f"❌ 공공데이터포털 응답 파싱 실패: {e}")
        return None


def _fetch_fund_nav_from_data_go_kr(fund_code: str):
    """공공데이터포털을 통해 펀드 최신 기준가(NAV) + 기준일자 수집.

    Args:
        fund_code: 펀드 표준코드 12자리 (예: 'K55234BX0537', 'KR5301AW7849')

    Returns:
        {"ticker_code": str, "price_date": date, "close_price": float (NAV per 1000좌)}
        또는 None (수집 실패 시)

    동작 흐름:
        1) 표준코드에서 srtnCd(5자리) 추출
        2) getStandardCodeInfo 호출 → srtnCd → asoStdCd 매핑 + basDt 확인
        3) 표준코드 일치 검증 (지정한 코드와 API 응답의 asoStdCd 일치 여부)
        4) 현재는 NAV 자체를 반환하지 않으므로 None 반환.
           (향후 NAV 조회 엔드포인트가 추가되면 이 자리에서 추가 호출)

    NOTE:
        2026-09 시점 데이터포털 금융위 펀드 API는 표준코드/기준일자만 반환하고
        NAV 자체는 반환하지 않습니다. 따라서 이 함수는 "수집 가능 여부 확인"까지만
        수행하며, NAV 데이터 부재 시 명시적으로 None을 반환합니다. fallback은
        fetch_fund_nav() 의 override 백엔드 또는 컨테이너 환경변수가 담당합니다.
    """
    if not is_fund_ticker(fund_code):
        print(f"⚠️ 펀드 티커 아님 (code={fund_code}) - 건너뜀")
        return None

    srtn_cd = extract_srtn_cd(fund_code)
    if not srtn_cd:
        print(f"⚠️ srtnCd 추출 실패 (code={fund_code})")
        return None

    info = _fetch_aso_std_cd_from_data_go_kr(srtn_cd)
    if not info:
        return None

    # 표준코드 검증: API 응답의 asoStdCd 와 입력 fund_code 가 일치해야 함
    api_aso = str(info.get("asoStdCd") or "").upper()
    if api_aso and api_aso != fund_code.upper():
        print(f"⚠️ 공공데이터포털 표준코드 불일치: 입력={fund_code} 응답={api_aso}")
        return None

    # 현재 시점 API 는 NAV 자체를 반환하지 않음 → 명시적 None
    # TODO: NAV 조회 엔드포인트 추가 시 본 함수에서 추가 호출 후 close_price 채우기
    bas_dt = info.get("basDt")  # 'YYYYMMDD' 형식
    print(
        f"ℹ️ 공공데이터포털 조회 성공 [{fund_code}]: "
        f"srtnCd={srtn_cd}, basDt={bas_dt}, fndNm={info.get('fndNm')!r}"
        f" → 단, NAV 데이터는 본 엔드포인트에서 미제공 → None 반환"
    )
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
