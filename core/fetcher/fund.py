"""
core/fetcher/fund.py
- 펀드닥터 NAV 스크래핑 및 기준가 수집 모듈
"""

import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from core.fetcher.kr_stock import upsert_daily_price
from database.repository import AssetRepository

_FUND_TICKER_PREFIXES = ("K55", "KR5")
FUNDDOCTOR_BASE_URL = "http://www.funddoctor.co.kr/afn/fund/fprofile.jsp"
FUNDDOCTOR_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

_FUND_NAV_OVERRIDES: dict = {}


def is_fund_ticker(ticker_code) -> bool:
    """IRP/연금 펀드 표준 코드 여부 판별.

    예: K55234BX0537 (KB투자 퇴직연금), KR5301AW7849 (미래에셋 퇴직연금)
    """
    if not ticker_code:
        return False
    code = str(ticker_code).strip().upper()
    return code.startswith(_FUND_TICKER_PREFIXES) and len(code) >= 10


def set_fund_nav_overrides(overrides: dict) -> None:
    """수동 펀드 NAV override dict 를 설정 (테스트 / 운영자 입력용)."""
    _FUND_NAV_OVERRIDES.clear()
    _FUND_NAV_OVERRIDES.update(overrides)


def get_fund_nav_overrides() -> dict:
    """현재 설정된 override dict 스냅샷 반환."""
    return dict(_FUND_NAV_OVERRIDES)


def _parse_funddoctor_price(text: str):
    if not text:
        return None
    cleaned = re.sub(r"[^\d.,]", "", text)
    cleaned = cleaned.replace(",", "")
    try:
        val = float(cleaned)
        if val > 0:
            return val
    except ValueError:
        pass
    return None


def _extract_funddoctor_price_date(soup):
    text = soup.get_text()
    date_patterns = [
        r"기준일\s*[:]\s*([0-9]{4}[./-]?[0-9]{2}[./-]?[0-9]{2})",
        r"([0-9]{4}년\s*[0-9]{1,2}월\s*[0-9]{1,2}일)",
        r"([0-9]{4}\.[0-9]{2}\.[0-9]{2})",
    ]
    candidates = []
    for pat in date_patterns:
        matches = re.findall(pat, text)
        for m in matches:
            cleaned_m = re.sub(r"[^0-9]", "", m)
            if len(cleaned_m) == 8:
                try:
                    dt = datetime.strptime(cleaned_m, "%Y%m%d").date()
                    candidates.append(dt)
                except ValueError:
                    continue
    if candidates:
        return max(candidates)
    return None


def _fetch_fund_nav_from_funddoctor(fund_code: str):
    """펀드닥터 HTML 스크래퍼로 펀드 NAV 수집."""
    if not is_fund_ticker(fund_code):
        return None

    code = str(fund_code).strip().upper()
    url = f"{FUNDDOCTOR_BASE_URL}?fund_cd={code}"
    headers = {"User-Agent": FUNDDOCTOR_USER_AGENT}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
    except Exception as e:
        print(f"❌ 펀드닥터 호출 실패 [{code}]: {e}")
        return None

    if resp.status_code != 200:
        return None

    resp.encoding = "utf-8"
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None

    # 기준가 영역: <div class="fund_price ...">1,562.19</div>
    price_el = soup.select_one("div.fund_price")
    if price_el is None:
        return None
    close_price = _parse_funddoctor_price(price_el.get_text(strip=True))
    if close_price is None:
        return None

    price_date = _extract_funddoctor_price_date(soup)
    if price_date is None:
        price_date = datetime.now().date()

    return {
        "ticker_code": code,
        "price_date": price_date,
        "close_price": close_price,
    }


def fetch_fund_nav(ticker_code: str):
    """
    펀드 기준가(NAV) 수집.
    """
    code = str(ticker_code).strip().upper()
    if not is_fund_ticker(code):
        print(f"⚠️ 펀드 티커가 아닙니다 (code={code}) - 건너뜀")
        return None

    if os.getenv("FUNDDOCTOR_FUND_FETCH_ENABLED", "1").strip() in {"1", "true", "yes"}:
        nav = _fetch_fund_nav_from_funddoctor(code)
        if nav:
            return nav

    if code in _FUND_NAV_OVERRIDES:
        entry = _FUND_NAV_OVERRIDES[code]
        nav = float(entry.get("nav", 0.0))
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

    print(f"⚠️ 펀드 NAV 수집 실패 [{code}] — 펀드닥터 미동작 + override 미설정")
    return None


def collect_fund_prices(verbose: bool = True) -> dict:
    """
    보유 종목 중 펀드 기준가를 수집하여 DB에 저장.
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()

    summary = {
        "fund_targets": 0,
        "fetched": 0,
        "saved": 0,
        "failed": 0,
        "results": [],
    }

    seen_codes: set = set()

    for h in holdings:
        code = h.get("ticker_code")
        name = h.get("ticker_name")

        if not is_fund_ticker(code):
            continue

        if code in seen_codes:
            continue
        seen_codes.add(code)

        summary["fund_targets"] += 1
        if verbose:
            print(f"📈 펀드 NAV 수집 시도: {name} ({code}) [FUND]")

        data = fetch_fund_nav(code)
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
            "asset_class": "fund",
        }
        summary["results"].append(result)
        if saved:
            summary["saved"] += 1
            if verbose:
                print(f"   ✅ 저장 완료: {code} {data['price_date']} ₩{data['close_price']:,.2f}")
        else:
            summary["failed"] += 1

    return summary
