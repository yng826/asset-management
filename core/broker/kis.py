"""한국투자증권(KIS) Open API 클라이언트 (조회 전용)

시세(일봉)·보유 잔고만 사용한다. 주문 계열은 구현하지 않는다. 체결 조회는 4b 단계에서 추가.
HTTP 200 이어도 본문 rt_cd != "0" 이면 오류다. 토큰은 24시간 유효, 발급은 1분 1회 제한이고
재발급해도 이전 토큰이 죽지는 않는다(6시간 내 재발급은 같은 토큰). 발급 때마다 알림톡이 가므로 DB 캐시를 쓴다.
"""

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

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
PENSION_KEYWORDS = ("연금저축", "IRP")  # 퇴직연금·연금저축 API 는 4c 단계


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


class KisClient:
    name = "kis"

    def __init__(self) -> None:
        self._accounts, self._price_cred = _load_accounts(
            KIS_BLOCKS, KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNTS
        )
        self._http = requests.Session()
        self._exchange_cache: dict[str, str] = {}
        self._last_call = 0.0

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

    def executions(self, start: date, end: date) -> list[Execution]:
        raise NotImplementedError("한투 체결 조회는 4b 단계에서 구현")

    def holdings(self) -> list[dict]:
        """일반·ISA 계좌의 국내 + 미국 보유 종목. 연금저축·IRP 는 4c 단계 전까지 건너뜀."""
        if not self._accounts:
            raise BrokerError("한투 계좌 미설정 (잔고 조회에 KIS_<번호>_ACCOUNT 필요)")
        result: list[dict] = []
        for acc in self._accounts:
            account_name, cred = acc.name, acc.cred
            if any(k in account_name for k in PENSION_KEYWORDS):
                logger.info(f"한투 {account_name}: 연금 계좌는 아직 지원하지 않아 건너뜀")
                continue
            base = {"CANO": acc.cano, "ACNT_PRDT_CD": acc.prdt}
            kr_pages = self._get_pages(
                cred,
                "/uapi/domestic-stock/v1/trading/inquire-balance",
                "TTTC8434R",
                {
                    **base,
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
            items = [i for p in kr_pages for i in _parse_kr_holdings(p)]
            try:
                us_pages = self._get_pages(
                    cred,
                    "/uapi/overseas-stock/v1/trading/inquire-balance",
                    "TTTS3012R",
                    {
                        **base,
                        "OVRS_EXCG_CD": "NASD",
                        "TR_CRCY_CD": "USD",
                        "CTX_AREA_FK200": "",
                        "CTX_AREA_NK200": "",
                    },
                    ("ctx_area_fk200", "ctx_area_nk200"),
                )
                items += [i for p in us_pages for i in _parse_us_holdings(p)]
            except BrokerError as e:
                logger.warning(f"한투 {account_name} 해외 잔고 조회 실패(해외 거래 불가 계좌일 수 있음): {e}")
            result += [{**i, "account_name": account_name} for i in items]
        return result
