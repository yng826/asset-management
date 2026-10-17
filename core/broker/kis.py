"""한국투자증권(KIS) Open API 클라이언트 (조회 전용)

시세(일봉)·보유 잔고만 사용한다. 주문 계열은 구현하지 않는다. 체결 조회는 4b 단계에서 추가.
HTTP 200 이어도 본문 rt_cd != "0" 이면 오류다. 토큰은 24시간 유효, 발급은 1분 1회 제한이고
재발급해도 이전 토큰이 죽지는 않는다(6시간 내 재발급은 같은 토큰). 발급 때마다 알림톡이 가므로 DB 캐시를 쓴다.
"""

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone

import requests

from config.settings import KIS_ACCOUNTS, KIS_APP_KEY, KIS_APP_SECRET

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


def _parse_accounts(raw: str) -> dict[str, tuple[str, str]]:
    """'한투 일반=12345678-01,한투 ISA=87654321-01' → {계좌명: (CANO, ACNT_PRDT_CD)}."""
    accounts: dict[str, tuple[str, str]] = {}
    for item in filter(None, (p.strip() for p in raw.split(","))):
        name, _, number = item.partition("=")
        m = re.fullmatch(r"(\d{8})-?(\d{2})", number.strip())
        if not name.strip() or not m:
            raise BrokerError(f"KIS_ACCOUNTS 형식 오류 (계좌명=12345678-01,… 형태여야 함): '{name.strip()}'")
        accounts[name.strip()] = (m.group(1), m.group(2))
    return accounts


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
        if not (KIS_APP_KEY and KIS_APP_SECRET):
            raise BrokerError("한투 키 미설정: KIS_APP_KEY / KIS_APP_SECRET 환경변수 필요")
        self._http = requests.Session()
        self._accounts = _parse_accounts(KIS_ACCOUNTS)
        self._exchange_cache: dict[str, str] = {}
        self._last_call = 0.0

    # --- 인증 / 요청 ---------------------------------------------------
    def _issue_token(self) -> tuple[str, int]:
        r = self._http.post(
            f"{BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            raise BrokerError(f"한투 토큰 발급 실패 HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        return body["access_token"], int(body.get("expires_in") or 86400)

    def _get(self, path: str, tr_id: str, params: dict, tr_cont: str = "") -> tuple[dict, dict]:
        """GET 호출 → (본문, 응답 헤더). 만료 토큰 1회 재발급, 초당 한도 초과는 최대 2회 재시도."""
        reissued = False
        retries = 0
        while True:
            wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            headers = {
                "authorization": f"Bearer {token_store.get_token(self.name, self._issue_token)}",
                "appkey": KIS_APP_KEY,
                "appsecret": KIS_APP_SECRET,
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
                token_store.invalidate(self.name)
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

    def _get_pages(self, path: str, tr_id: str, params: dict, ctx_keys: tuple[str, str]) -> list[dict]:
        """tr_cont 연속 조회(M/F 이면 다음 페이지). 각 페이지 본문 목록 반환."""
        fk, nk = ctx_keys
        pages: list[dict] = []
        tr_cont = ""
        for _ in range(MAX_PAGES):
            body, headers = self._get(path, tr_id, params, tr_cont)
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
            raise BrokerError("KIS_ACCOUNTS 미설정 (잔고 조회에 계좌번호 필요)")
        result: list[dict] = []
        for account_name, (cano, prdt) in self._accounts.items():
            if any(k in account_name for k in PENSION_KEYWORDS):
                logger.info(f"한투 {account_name}: 연금 계좌는 아직 지원하지 않아 건너뜀")
                continue
            base = {"CANO": cano, "ACNT_PRDT_CD": prdt}
            kr_pages = self._get_pages(
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
