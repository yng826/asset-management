"""한국투자증권(KIS) Open API 클라이언트 (조회 전용)

시세(일봉)·보유 잔고·체결 조회만 사용한다. 주문 계열은 구현하지 않는다.
HTTP 200 이어도 본문 rt_cd != "0" 이면 오류다. 토큰은 24시간 유효, 발급은 1분 1회 제한이고
재발급해도 이전 토큰이 죽지는 않는다(6시간 내 재발급은 같은 토큰). 발급 때마다 알림톡이 가므로 DB 캐시를 쓴다.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from config.constants import ACCOUNT_ALIASES, ASSET_MAP
from config.settings import KIS_ACCOUNTS, KIS_APP_KEY, KIS_APP_SECRET, KIS_BLOCKS

from . import token_store
from .base import BrokerError, Close, Execution

logger = logging.getLogger(__name__)

BASE_URL = "https://openapi.koreainvestment.com:9443"
TIMEOUT = 10
MIN_INTERVAL = 0.12  # 실전 초당 한도(약 20건) 보호용 호출 간격
MAX_RETRIES_RATE = 2
MAX_PAGES = 10
KST = timezone(timedelta(hours=9))
US_EXCHANGES = ("NAS", "NYS", "AMS")  # 해외 일봉 거래소 코드, 앞에서부터 시도
TOKEN_ERROR_CODES = {"EGW00121", "EGW00123"}  # 무효·만료 토큰
RATE_ERROR_CODE = "EGW00201"  # 초당 거래건수 초과
IRP_KEYWORD = "IRP"  # 퇴직연금 잔고 API 사용
PENSION_SAVINGS_KEYWORD = "연금저축"  # 전용 API 미확인: 일반 국내 잔고 API 를 시험
# 퇴직연금 잔고 응답의 수량 필드 후보 (예제에 필드명이 없어 순서대로 시도)
QTY_FIELDS = ("hldg_qty", "cblc_qty")
TICKER_RE = re.compile(r"[0-9A-Za-z]{6,12}")
NY = ZoneInfo("America/New_York")
KR_CLOSE = (15, 40)  # 국내 정규장 마감 + 여유 (이후엔 잔량이 남아도 더 체결되지 않음)
US_CLOSE = (16, 5)  # 미국 정규장 마감(뉴욕 현지) + 여유
CCLD_INNER_DAYS = 90  # 국내 일별체결조회 3개월 이내 TR(TTTC0081R) 범위


@dataclass(frozen=True)
class _Cred:
    """앱키·시크릿 한 쌍과 그 토큰을 api_tokens 에 저장할 이름 (한투 앱키는 계좌마다 따로 발급)."""

    token_name: str
    app_key: str
    app_secret: str


@dataclass(frozen=True)
class _Account:
    name: str
    cano: str
    prdt: str
    cred: _Cred


def _canonical_name(name: str, label: str) -> str:
    """계좌명을 config/constants.py 의 정식 이름(transactions.account_name)으로 맞춘다. 모르는 이름이면 중단."""
    name = ACCOUNT_ALIASES.get(name, name)
    if name not in ASSET_MAP:
        raise BrokerError(
            f"{label} 계좌명 '{name}' 이(가) config/constants.py ASSET_MAP 에 없습니다: {', '.join(ASSET_MAP)}"
        )
    return name


def _split_account_no(number: str, label: str) -> tuple[str, str]:
    m = re.fullmatch(r"(\d{8})-?(\d{2})", number.strip())
    if not m:
        raise BrokerError(f"{label} 계좌번호 형식 오류 (12345678-01 형태여야 함)")
    return m.group(1), m.group(2)


def _load_accounts(
    blocks: dict[int, dict[str, str]], legacy_key: str, legacy_secret: str, legacy_accounts: str
) -> tuple[list[_Account], _Cred]:
    """(계좌 목록, 시세 조회용 키). KIS_<번호>_* 가 있으면 그것을, 없으면 예전 단일 키 + KIS_ACCOUNTS 를 쓴다."""
    if blocks:
        accounts = []
        for n, b in blocks.items():
            missing = [k for k in ("NAME", "ACCOUNT", "APP_KEY", "APP_SECRET") if not b.get(k)]
            if missing:
                raise BrokerError(
                    f"KIS_{n}_* 설정에 빠진 항목: " + ", ".join(f"KIS_{n}_{k}" for k in missing)
                )
            cano, prdt = _split_account_no(b["ACCOUNT"], f"KIS_{n}_ACCOUNT")
            accounts.append(
                _Account(
                    _canonical_name(b["NAME"], f"KIS_{n}_NAME"),
                    cano,
                    prdt,
                    _Cred(f"kis{n}", b["APP_KEY"], b["APP_SECRET"]),
                )
            )
        return accounts, accounts[0].cred
    if not (legacy_key and legacy_secret):
        raise BrokerError("한투 키 미설정: KIS_1_NAME/ACCOUNT/APP_KEY/APP_SECRET 환경변수 필요")
    cred = _Cred("kis", legacy_key, legacy_secret)
    accounts = []
    for item in filter(None, (p.strip() for p in legacy_accounts.split(","))):
        name, _, number = item.partition("=")
        if not name.strip():
            raise BrokerError("KIS_ACCOUNTS 형식 오류 (계좌명=12345678-01,… 형태여야 함)")
        cano, prdt = _split_account_no(number, f"KIS_ACCOUNTS '{name.strip()}'")
        accounts.append(_Account(_canonical_name(name.strip(), "KIS_ACCOUNTS"), cano, prdt, cred))
    return accounts, cred


def is_kr_symbol(symbol: str) -> bool:
    """국내(6자리 숫자·영문 혼용) 여부. 미국 티커는 영문 1~5자리."""
    return len(symbol) == 6


def _parse_kr_closes(payload: dict) -> list[Close]:
    """국내 기간별 시세 응답(output2, 최신순) → 오래된 순 [{price_date, close_price}]."""
    rows = [
        {
            "price_date": datetime.strptime(r["stck_bsop_date"], "%Y%m%d").date(),
            "close_price": float(r["stck_clpr"]),
        }
        for r in payload.get("output2") or []
        if r.get("stck_bsop_date")
    ]
    rows.sort(key=lambda r: r["price_date"])
    return rows


def _parse_us_closes(payload: dict) -> list[Close]:
    """해외 기간별 시세 응답(output2, 최신순) → 오래된 순."""
    rows = [
        {"price_date": datetime.strptime(r["xymd"], "%Y%m%d").date(), "close_price": float(r["clos"])}
        for r in payload.get("output2") or []
        if r.get("xymd") and r.get("clos")
    ]
    rows.sort(key=lambda r: r["price_date"])
    return rows


def _parse_kr_holdings(payload: dict) -> list[dict]:
    return [
        {"symbol": r["pdno"], "name": r["prdt_name"], "quantity": float(r["hldg_qty"]), "currency": "KRW"}
        for r in payload.get("output1") or []
        if float(r.get("hldg_qty") or 0) > 0
    ]


def _parse_us_holdings(payload: dict) -> list[dict]:
    return [
        {
            "symbol": r["ovrs_pdno"],
            "name": r["ovrs_item_name"],
            "quantity": float(r["ovrs_cblc_qty"]),
            "currency": "USD",
        }
        for r in payload.get("output1") or []
        if float(r.get("ovrs_cblc_qty") or 0) > 0
    ]


def _pick_qty(row: dict) -> float | None:
    for k in QTY_FIELDS:
        if row.get(k) not in (None, ""):
            return float(row[k])
    return None


def _parse_pension_holdings(payload: dict) -> tuple[list[dict], list[str]]:
    """퇴직연금 잔고 응답 → (보유 종목, 건너뛴 행의 상품명). 종목코드가 없는 상품(정기예금 등)은 건너뛴다."""
    items: list[dict] = []
    skipped: list[str] = []
    for r in payload.get("output1") or []:
        code = (r.get("pdno") or "").strip()
        name = (r.get("prdt_name") or "").strip()
        if not TICKER_RE.fullmatch(code):
            skipped.append(name or "(이름 없음)")
            continue
        qty = _pick_qty(r)
        if qty is None:
            raise BrokerError(f"퇴직연금 잔고 수량 필드를 찾지 못함, 응답 필드명: {', '.join(sorted(r))}")
        if qty > 0:
            items.append({"symbol": code, "name": name, "quantity": qty, "currency": "KRW"})
    return items, skipped


def _describe_pension_rows(account_name: str, payload: dict) -> list[str]:
    """퇴직연금 잔고 응답의 원본 행을 점검용 문자열로 요약 (계좌번호가 섞이지 않도록 상품명·종목코드·수량 필드만)."""
    rows = payload.get("output1") or []
    lines = [
        f"{account_name} output1 {len(rows)}행, output2 필드: {', '.join(sorted(_as_dict(payload.get('output2'))))}"
    ]
    for r in rows:
        qty = ", ".join(f"{k}={v}" for k, v in sorted(r.items()) if "qty" in k)
        lines.append(f"{account_name} pdno={r.get('pdno')!r} 상품명={r.get('prdt_name')!r} {qty}")
    return lines


def _num(row: dict, *keys: str) -> float:
    """응답 문자열 숫자 필드(여러 이름 후보 중 첫 값). 없거나 비어 있으면 0.

    >>> _num({"a": " 3.5 "}, "x", "a")
    3.5
    >>> _num({"a": ""}, "a")
    0.0
    """
    for k in keys:
        v = str(row.get(k) or "").strip()
        if v:
            try:
                return float(v.replace(",", ""))
            except ValueError:
                return 0.0
    return 0.0


def _root_id(odno: str, parents: dict[str, str]) -> str:
    """정정·재정정 사슬(orgn_odno)을 따라 최초 주문번호를 찾는다. 사슬이 끊기면 거기까지.

    >>> _root_id("3", {"3": "2", "2": "1", "1": "0000000000"})
    '1'
    >>> _root_id("5", {"5": "5"})
    '5'
    """
    cur = odno
    for _ in range(10):
        parent = parents.get(cur, "")
        if not parent or parent == cur or parent.strip("0") == "" or parent not in parents:
            break
        cur = parent
    return cur


def _group_orders(
    orders: list[dict], account_name: str, currency: str, market_closed: bool
) -> list[Execution]:
    """주문 행들(공통 키로 정규화됨)을 최초 주문 기준으로 묶어 체결 1건으로 만든다.

    orders 항목: date, odno, orgn_odno, side(BUY/SELL), symbol, ccld_qty, avg_price, remaining(미체결 잔량, 취소는 0).
    체결수량이 0인 묶음(취소·미체결)은 버린다. 미체결 잔량이 남은 묶음은 장 마감 전이면 보류한다(수량이 아직 변하므로).
    수량은 합산, 단가는 수량가중평균.

    >>> o = {"date": date(2026, 10, 12), "side": "BUY", "symbol": "005930", "avg_price": 70000.0, "remaining": 0.0}
    >>> r = _group_orders([{**o, "odno": "1", "orgn_odno": "0", "ccld_qty": 4.0, "avg_price": 70000.0},
    ...                    {**o, "odno": "2", "orgn_odno": "1", "ccld_qty": 6.0, "avg_price": 70100.0}], "한투 일반", "KRW", False)
    >>> (r[0]["order_id"], r[0]["quantity"], r[0]["avg_price"])
    ('1', 10.0, 70060.0)
    >>> _group_orders([{**o, "odno": "1", "orgn_odno": "0", "ccld_qty": 4.0, "remaining": 6.0}], "한투 일반", "KRW", False)
    []
    """
    parents = {o["odno"]: o["orgn_odno"] for o in orders}
    groups: dict[tuple, list[dict]] = {}
    for o in orders:
        groups.setdefault((o["date"], _root_id(o["odno"], parents), o["symbol"], o["side"]), []).append(o)
    result: list[Execution] = []
    for (d, root, symbol, side), rows in groups.items():
        qty = sum(r["ccld_qty"] for r in rows)
        if qty <= 0 or side not in ("BUY", "SELL"):
            continue
        if not market_closed and any(r["remaining"] > 0 for r in rows):
            continue
        avg = sum(r["ccld_qty"] * r["avg_price"] for r in rows) / qty
        result.append(
            {
                "broker": "kis",
                "order_id": root,
                "account_name": account_name,
                "trade_date": d,
                "symbol": symbol,
                "side": side,
                "quantity": qty,
                "avg_price": round(avg, 4),
                "currency": currency,
            }
        )
    return result


def _group_orders_by_day(
    orders: list[dict], account_name: str, currency: str, tz, close: tuple[int, int]
) -> list[Execution]:
    """주문일별로 장 마감 여부를 판단해 _group_orders 를 적용 (오늘 주문만 마감 전일 수 있음)."""
    result: list[Execution] = []
    for d in sorted({o["date"] for o in orders}):
        day = [o for o in orders if o["date"] == d]
        result += _group_orders(day, account_name, currency, _market_closed(d, tz, close))
    return result


def _parse_ymd(value: str) -> date | None:
    try:
        return datetime.strptime(str(value).strip(), "%Y%m%d").date()
    except ValueError:
        return None


def _side(code: str) -> str:
    return {"01": "SELL", "02": "BUY"}.get(str(code).strip(), "")


def _parse_kr_orders(payload: dict) -> list[dict]:
    """국내 일별주문체결조회 output1 → 공통 주문 행. 체결수량 없는 행도 포함(정정 사슬 복원용)."""
    rows = []
    for r in payload.get("output1") or []:
        d = _parse_ymd(r.get("ord_dt", ""))
        odno = str(r.get("odno") or "").strip()
        symbol = str(r.get("pdno") or "").strip()
        if not (d and odno and symbol):
            continue
        cancelled = str(r.get("cncl_yn") or "").strip().upper() == "Y"
        rows.append(
            {
                "date": d,
                "odno": odno,
                "orgn_odno": str(r.get("orgn_odno") or "").strip(),
                "side": _side(r.get("sll_buy_dvsn_cd", "")),
                "symbol": symbol,
                "ccld_qty": _num(r, "tot_ccld_qty"),
                "avg_price": _num(r, "avg_prvs"),
                "remaining": 0.0 if cancelled else _num(r, "rmn_qty"),
            }
        )
    return rows


def _parse_us_orders(payload: dict) -> list[dict]:
    """해외 주문체결내역 output → 공통 주문 행. 취소(rvse_cncl_dvsn=02)는 잔량 0 취급."""
    out = payload.get("output") or []
    if isinstance(out, dict):
        out = [out]
    rows = []
    for r in out:
        d = _parse_ymd(r.get("ord_dt", ""))
        odno = str(r.get("odno") or "").strip()
        symbol = str(r.get("pdno") or "").strip()
        if not (d and odno and symbol):
            continue
        cancelled = str(r.get("rvse_cncl_dvsn") or "").strip() == "02" or "취소" in str(
            r.get("prcs_stat_name") or ""
        )
        rows.append(
            {
                "date": d,
                "odno": odno,
                "orgn_odno": str(r.get("orgn_odno") or "").strip(),
                "side": _side(r.get("sll_buy_dvsn_cd", "")),
                "symbol": symbol,
                "ccld_qty": _num(r, "ft_ccld_qty"),
                "avg_price": _num(r, "ft_ccld_unpr3"),
                "remaining": 0.0 if cancelled else _num(r, "nccs_qty"),
            }
        )
    return rows


def _market_closed(order_date: date, tz, close: tuple[int, int]) -> bool:
    """해당 시장의 주문일 장이 이미 마감됐는지 (주문일이 지났거나 오늘 마감 시각 이후)."""
    now = datetime.now(tz)
    if order_date < now.date():
        return True
    return order_date == now.date() and (now.hour, now.minute) >= close


def _as_dict(value: object) -> dict:
    """output2 는 dict 또는 [dict] 로 오므로 첫 dict 만 쓴다."""
    if isinstance(value, list):
        value = value[0] if value else {}
    return value if isinstance(value, dict) else {}


class KisClient:
    name = "kis"

    def __init__(self) -> None:
        self._accounts, self._price_cred = _load_accounts(
            KIS_BLOCKS, KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNTS
        )
        self._http = requests.Session()
        self._exchange_cache: dict[str, str] = {}
        self._last_call = 0.0
        self.skipped: list[tuple[str, str]] = []  # (계좌명, 상품명) holdings() 에서 종목코드가 없어 건너뛴 행
        self.failed: list[tuple[str, str]] = []  # (계좌명, 사유) holdings() 에서 조회 실패한 계좌
        self.raw_pension: list[str] = []  # 퇴직연금 잔고 응답 점검용 요약 (상품명·종목코드·수량 필드만)

    # --- 인증 / 요청 ---------------------------------------------------
    def _issue_token(self, cred: _Cred) -> tuple[str, int]:
        r = self._http.post(
            f"{BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": cred.app_key, "appsecret": cred.app_secret},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            raise BrokerError(f"한투 토큰 발급 실패 HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        return body["access_token"], int(body.get("expires_in") or 86400)

    def _get(self, cred: _Cred, path: str, tr_id: str, params: dict, tr_cont: str = "") -> tuple[dict, dict]:
        """GET 호출 → (본문, 응답 헤더). 만료 토큰 1회 재발급, 초당 한도 초과는 최대 2회 재시도."""
        reissued = False
        retries = 0
        while True:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            headers = {
                "authorization": f"Bearer {token_store.get_token(cred.token_name, lambda: self._issue_token(cred))}",
                "appkey": cred.app_key,
                "appsecret": cred.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
                "tr_cont": tr_cont,
            }
            try:
                r = self._http.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=TIMEOUT)
            except requests.RequestException as e:
                raise BrokerError(f"한투 요청 실패 {path}: {e}") from e
            self._last_call = time.monotonic()
            try:
                body = r.json()
            except ValueError:
                body = {}
            code = body.get("msg_cd", "")
            if code in TOKEN_ERROR_CODES and not reissued:
                token_store.invalidate(cred.token_name)
                reissued = True
                continue
            if code == RATE_ERROR_CODE and retries < MAX_RETRIES_RATE:
                retries += 1
                logger.warning(f"한투 초당 한도 초과, 1초 후 재시도 ({retries}/{MAX_RETRIES_RATE})")
                time.sleep(1)
                continue
            if r.status_code != 200 or body.get("rt_cd") != "0":
                raise BrokerError(
                    f"한투 {path} HTTP {r.status_code} {code} {body.get('msg1') or r.text[:200]}"
                )
            return body, r.headers

    def _get_pages(
        self, cred: _Cred, path: str, tr_id: str, params: dict, ctx_keys: tuple[str, str]
    ) -> list[dict]:
        """tr_cont 연속 조회(M/F 이면 다음 페이지). 각 페이지 본문 목록 반환."""
        fk, nk = ctx_keys
        pages: list[dict] = []
        tr_cont = ""
        for _ in range(MAX_PAGES):
            body, headers = self._get(cred, path, tr_id, params, tr_cont)
            pages.append(body)
            if headers.get("tr_cont") not in ("M", "F"):
                break
            params = {**params, fk.upper(): body.get(fk, ""), nk.upper(): body.get(nk, "")}
            tr_cont = "N"
        else:
            logger.warning(f"한투 {path} 페이지가 {MAX_PAGES}개를 넘어 잘랐습니다")
        return pages

    # --- 조회 ----------------------------------------------------------
    def daily_closes(self, symbol: str, days: int) -> list[Close]:
        days = min(max(days, 1), 60)
        if is_kr_symbol(symbol):
            end = datetime.now(KST).date()
            body, _ = self._get(
                self._price_cred,
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                "FHKST03010100",
                {
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_DATE_1": (end - timedelta(days=days * 2 + 10)).strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "1",  # 원주가 (토스 adjusted=false 와 같은 기준)
                },
            )
            return _parse_kr_closes(body)[-days:]
        return self._us_closes(symbol)[-days:]

    def _us_closes(self, symbol: str) -> list[Close]:
        """해외 일봉. 거래소 코드를 모르므로 NAS→NYS→AMS 순서로 첫 비어 있지 않은 응답을 쓴다."""
        known = self._exchange_cache.get(symbol)
        for excd in (known,) if known else US_EXCHANGES:
            body, _ = self._get(
                self._price_cred,
                "/uapi/overseas-price/v1/quotations/dailyprice",
                "HHDFS76240000",
                {"AUTH": "", "EXCD": excd, "SYMB": symbol, "GUBN": "0", "BYMD": "", "MODP": "0"},
            )
            closes = _parse_us_closes(body)
            if closes:
                self._exchange_cache[symbol] = excd
                return closes
        return []

    def _kr_orders(self, acc: _Account, start: date, end: date) -> list[dict]:
        start = max(start, end - timedelta(days=CCLD_INNER_DAYS))  # 3개월 이내 TR 만 사용
        pages = self._get_pages(
            acc.cred,
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "TTTC0081R",
            {
                "CANO": acc.cano,
                "ACNT_PRDT_CD": acc.prdt,
                "INQR_STRT_DT": start.strftime("%Y%m%d"),
                "INQR_END_DT": end.strftime("%Y%m%d"),
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": "",
                "CCLD_DVSN": "00",
                "INQR_DVSN": "01",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_1": "",
                "EXCG_ID_DVSN_CD": "KRX",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            ("ctx_area_fk100", "ctx_area_nk100"),
        )
        return [o for p in pages for o in _parse_kr_orders(p)]

    def _us_orders(self, acc: _Account, start: date, end: date) -> list[dict]:
        pages = self._get_pages(
            acc.cred,
            "/uapi/overseas-stock/v1/trading/inquire-ccnl",
            "TTTS3035R",
            {
                "CANO": acc.cano,
                "ACNT_PRDT_CD": acc.prdt,
                "PDNO": "%",
                "ORD_STRT_DT": (start - timedelta(days=1)).strftime(
                    "%Y%m%d"
                ),  # 현지(뉴욕) 날짜 기준이라 하루 여유
                "ORD_END_DT": end.strftime("%Y%m%d"),
                "SLL_BUY_DVSN": "00",
                "CCLD_NCCS_DVSN": "00",
                "OVRS_EXCG_CD": "NASD",
                "SORT_SQN": "DS",
                "ORD_DT": "",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "CTX_AREA_NK200": "",
                "CTX_AREA_FK200": "",
            },
            ("ctx_area_fk200", "ctx_area_nk200"),
        )
        return [o for p in pages for o in _parse_us_orders(p)]

    def executions(self, start: date, end: date) -> list[Execution]:
        """계좌별 체결. 정정·재정정은 최초 주문 기준 한 건으로 묶고, 취소·미체결은 제외한다.
        미체결 잔량이 남은 주문은 장 마감 전까지 보류(체결이 진행 중이라 수량이 바뀜).
        IRP 는 체결 조회 대상이 아니다. 계좌 하나가 실패하면 경고만 남기고 다음으로(self.failed)."""
        if not self._accounts:
            raise BrokerError("한투 계좌 미설정 (체결 조회에 KIS_<번호>_ACCOUNT 필요)")
        self.failed = []
        result: list[Execution] = []
        for acc in self._accounts:
            if IRP_KEYWORD in acc.name:
                continue
            try:
                kr = self._kr_orders(acc, start, end)
                result += _group_orders_by_day(kr, acc.name, "KRW", ZoneInfo("Asia/Seoul"), KR_CLOSE)
                if PENSION_SAVINGS_KEYWORD in acc.name:
                    continue  # 연금저축은 해외 거래 없음
                try:
                    us = self._us_orders(acc, start, end)
                    result += _group_orders_by_day(us, acc.name, "USD", NY, US_CLOSE)
                except BrokerError as e:
                    logger.warning(f"한투 {acc.name} 해외 체결 조회 실패(해외 거래 불가 계좌일 수 있음): {e}")
            except BrokerError as e:
                logger.warning(f"한투 {acc.name} 체결 조회 실패, 다음 계좌로 진행: {e}")
                self.failed.append((acc.name, str(e)))
        return result

    def _kr_balance_pages(self, acc: _Account) -> list[dict]:
        return self._get_pages(
            acc.cred,
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            "TTTC8434R",
            {
                "CANO": acc.cano,
                "ACNT_PRDT_CD": acc.prdt,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            ("ctx_area_fk100", "ctx_area_nk100"),
        )

    def _irp_items(self, acc: _Account) -> list[dict]:
        pages = self._get_pages(
            acc.cred,
            "/uapi/domestic-stock/v1/trading/pension/inquire-balance",
            "TTTC2208R",
            {
                "CANO": acc.cano,
                "ACNT_PRDT_CD": acc.prdt,
                "ACCA_DVSN_CD": "00",
                "INQR_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
            ("ctx_area_fk100", "ctx_area_nk100"),
        )
        items: list[dict] = []
        for p in pages:
            self.raw_pension += _describe_pension_rows(acc.name, p)
            got, skipped = _parse_pension_holdings(p)
            items += got
            self.skipped += [(acc.name, n) for n in skipped]
        return items

    def _account_items(self, acc: _Account) -> list[dict]:
        if IRP_KEYWORD in acc.name:
            return self._irp_items(acc)
        items = [i for p in self._kr_balance_pages(acc) for i in _parse_kr_holdings(p)]
        if PENSION_SAVINGS_KEYWORD in acc.name:
            return items  # 연금저축은 해외 잔고 호출 안 함
        try:
            us_pages = self._get_pages(
                acc.cred,
                "/uapi/overseas-stock/v1/trading/inquire-balance",
                "TTTS3012R",
                {
                    "CANO": acc.cano,
                    "ACNT_PRDT_CD": acc.prdt,
                    "OVRS_EXCG_CD": "NASD",
                    "TR_CRCY_CD": "USD",
                    "CTX_AREA_FK200": "",
                    "CTX_AREA_NK200": "",
                },
                ("ctx_area_fk200", "ctx_area_nk200"),
            )
            items += [i for p in us_pages for i in _parse_us_holdings(p)]
        except BrokerError as e:
            logger.warning(f"한투 {acc.name} 해외 잔고 조회 실패(해외 거래 불가 계좌일 수 있음): {e}")
        return items

    def holdings(self) -> list[dict]:
        """계좌별 보유 종목. IRP 는 퇴직연금 잔고 API, 연금저축은 일반 국내 잔고 API 시험.
        계좌 하나가 실패하면 경고만 남기고 다음 계좌로 넘어간다(self.failed 에 기록). 종목코드 없는 행은 self.skipped."""
        if not self._accounts:
            raise BrokerError("한투 계좌 미설정 (잔고 조회에 KIS_<번호>_ACCOUNT 필요)")
        self.skipped, self.failed, self.raw_pension = [], [], []
        result: list[dict] = []
        for acc in self._accounts:
            try:
                items = self._account_items(acc)
            except BrokerError as e:
                logger.warning(f"한투 {acc.name} 잔고 조회 실패, 다음 계좌로 진행: {e}")
                self.failed.append((acc.name, str(e)))
                continue
            result += [{**i, "account_name": acc.name} for i in items]
        return result
