"""
core/ticker_master.py
- 종목 마스터(국내 주식/ETF, 미국 주식, 업비트 원화마켓)를 DB(ticker_master)에 주기 동기화
- 이름·코드·별칭 검색 (텔레그램 버튼 거래 입력에서 종목을 오타 없이 확정하기 위해 사용)
"""

import logging
import re

from config.constants import TICKER_ALIASES, TICKER_MAP
from core.fetcher.kr_stock import is_kr_stock_ticker
from database.repository import AssetRepository

# 시장 구분 → 표시 라벨
MARKET_LABELS = {"KR": "국내", "ETF": "ETF", "US": "미국", "CRYPTO": "코인", "FUND": "펀드"}

# 동점 시 시장 우선순위
_MARKET_ORDER = {"KR": 0, "ETF": 1, "CRYPTO": 2, "US": 3, "FUND": 4}

# 동기화 결과가 이보다 적으면 소스 장애로 보고 기존 데이터 유지
_MIN_ROWS = {"KR": 1000, "ETF": 300, "US": 1000, "CRYPTO": 50}

# 검색어 토큰 한글 표기 → 상품명 표기 (ETF 브랜드 등)
_TOKEN_ALIASES = {
    "코덱스": "KODEX",
    "타이거": "TIGER",
    "라이즈": "RISE",
    "에이스": "ACE",
    "솔": "SOL",
    "플러스": "PLUS",
    "키움": "KIWOOM",
    "에스앤피": "S&P",
}

_US_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z.\-]{0,6}$")
_FUND_CODE_PATTERN = re.compile(r"^K[A-Z0-9]{11}$")


def classify_market(ticker_code: str) -> str:
    """종목코드 형태로 시장 구분 (KR / CRYPTO / FUND / US)."""
    code = str(ticker_code or "").strip()
    if code.startswith("KRW-"):
        return "CRYPTO"
    if is_kr_stock_ticker(code):
        return "KR"
    if _FUND_CODE_PATTERN.match(code):
        return "FUND"
    return "US"


def currency_for_market(market: str) -> str:
    return "USD" if market == "US" else "KRW"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).upper()


def _fetch_listings() -> dict[str, list[tuple[str, str]]]:
    """시장별 상장 종목 목록 수집. 실패한 시장은 결과에서 빠짐."""
    listings: dict[str, list[tuple[str, str]]] = {}

    try:
        import FinanceDataReader as fdr

        df = fdr.StockListing("KRX")
        listings["KR"] = [(str(r.Code).strip(), str(r.Name).strip()) for r in df.itertuples()]
    except Exception as e:
        logging.warning(f"⚠️ 국내 주식 목록 수집 실패: {e}")

    try:
        import FinanceDataReader as fdr

        df = fdr.StockListing("ETF/KR")
        listings["ETF"] = [(str(r.Symbol).strip(), str(r.Name).strip()) for r in df.itertuples()]
    except Exception as e:
        logging.warning(f"⚠️ 국내 ETF 목록 수집 실패: {e}")

    try:
        import pyupbit

        markets = pyupbit.get_tickers(fiat="KRW", verbose=True) or []
        listings["CRYPTO"] = [(m["market"], m["korean_name"]) for m in markets]
    except Exception as e:
        logging.warning(f"⚠️ 업비트 목록 수집 실패: {e}")

    try:
        import FinanceDataReader as fdr

        us = []
        for exchange in ("NASDAQ", "NYSE", "AMEX"):
            df = fdr.StockListing(exchange)
            us.extend((str(r.Symbol).strip(), str(r.Name).strip()) for r in df.itertuples())
        listings["US"] = us
    except Exception as e:
        logging.warning(f"⚠️ 미국 주식 목록 수집 실패: {e}")

    return listings


def sync_ticker_master() -> dict[str, int]:
    """종목 마스터 DB 동기화 (시장 단위 교체). 반환: {market: 적재 건수} (건너뛴 시장은 0)."""
    repo = AssetRepository()
    repo.ensure_ticker_master_table()

    result = {}
    for market, items in _fetch_listings().items():
        items = [(code, name) for code, name in items if code and name]
        if len(items) < _MIN_ROWS.get(market, 1):
            logging.warning(f"⚠️ 종목 마스터 {market} {len(items)}건 — 기준 미달로 기존 데이터 유지")
            result[market] = 0
            continue
        result[market] = len(items) if repo.replace_ticker_master(market, items) else 0

    logging.info(f"📚 종목 마스터 동기화 완료: {result}")
    return result


def ensure_ticker_master() -> None:
    """테이블이 없거나 비어 있으면 1회 동기화 (봇 기동 시 호출)."""
    repo = AssetRepository()
    repo.ensure_ticker_master_table()
    if repo.count_ticker_master() == 0:
        sync_ticker_master()


def search_tickers(query: str, limit: int = 8) -> list[dict]:
    """종목명/코드/별칭 검색 (DB ticker_master + 과거 거래 종목).

    - 검색어를 공백으로 나눈 모든 토큰이 이름에 포함되어야 함 (예: 'kodex 나스닥' → KODEX 미국나스닥100)
    - 점수: 코드·별칭 일치(0) < 이름 일치(1) < 이름 접두(2) < 이름 포함(3)
    - 동점이면 과거 거래 종목 → 시장(국내/ETF/코인/미국) → 짧은 이름 순
    - 과거 거래가 있는 종목은 그때 쓴 이름으로 표시 (보유 집계가 이름 단위로 묶이므로)

    Returns:
        [{"code", "name", "market", "traded"}, ...]
    """
    q = _normalize(query)
    if not q:
        return []

    tokens = [_TOKEN_ALIASES.get(t, t.upper()) for t in str(query).split()]
    tokens = [_normalize(t) for t in tokens if t]
    q_joined = "".join(tokens)

    codes = {q}
    for alias, code in TICKER_ALIASES.items():
        if _normalize(alias) == q:
            codes.add(code)
    for name, code in TICKER_MAP.items():
        if _normalize(name) == q:
            codes.add(code.split(".")[0])

    repo = AssetRepository()
    traded = {t["ticker_code"]: t["ticker_name"] for t in repo.get_traded_tickers()}

    candidates = repo.search_ticker_master(tokens, sorted(codes))
    # 마스터에 없는 과거 거래 종목(펀드·일부 미국 ETF 등)도 검색 대상
    master_codes = {c["code"] for c in candidates}
    for code, name in traded.items():
        if code not in master_codes:
            name_n = _normalize(name)
            if code in codes or all(t in name_n for t in tokens):
                candidates.append({"code": code, "name": name, "market": classify_market(code)})

    scored = []
    for item in candidates:
        is_traded = item["code"] in traded
        if is_traded:
            item = {**item, "name": traded[item["code"]]}
        name = _normalize(item["name"])
        if item["code"] in codes:
            score = 0
        elif name == q_joined:
            score = 1
        elif name.startswith(q_joined):
            score = 2
        else:
            score = 3
        sort_key = (score, not is_traded, _MARKET_ORDER.get(item["market"], 9), len(name))
        scored.append((sort_key, {**item, "traded": is_traded}))

    scored.sort(key=lambda s: s[0])
    return [item for _, item in scored[:limit]]


def resolve_display_name(item: dict) -> str:
    """저장할 종목명: 과거 거래 이름(검색 결과에 이미 반영) 유지, 미국 신규 종목은 한글 별칭 우선."""
    if item.get("traded"):
        return item["name"]
    if item["market"] == "US":
        for alias, code in TICKER_ALIASES.items():
            if code == item["code"]:
                return alias
    return item["name"]


def is_us_symbol_candidate(query: str) -> bool:
    """마스터에 없는 미국 티커(ETF 등)를 직접 지정할 수 있는 형태인지 (예: QQQ, BRK.B)."""
    return bool(_US_SYMBOL_PATTERN.match(str(query or "").strip().upper()))
