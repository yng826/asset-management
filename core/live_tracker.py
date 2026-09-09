import logging
import re
from collections import defaultdict
from datetime import datetime, time

import FinanceDataReader as fdr
import pytz
import pyupbit

from core.calculator import get_current_fx_rate, get_latest_prices_map
from database.repository import AssetRepository

logger = logging.getLogger(__name__)
KST = pytz.timezone("Asia/Seoul")


def get_market_status():
    """
    KST 기준 현재 시장 운영 상태 판별
    Returns:
        dict: {'kr': bool, 'us': bool, 'crypto': bool}
    """
    now_utc = datetime.now(pytz.utc)
    now_kst = now_utc.astimezone(KST)
    now_time = now_kst.time()
    weekday = now_kst.weekday()

    is_weekday = weekday < 5

    kr_open = is_weekday and time(9, 0) <= now_time <= time(15, 30)
    us_open = is_weekday and (now_time >= time(22, 30) or now_time <= time(5, 0))

    return {
        "kr": kr_open,
        "us": us_open,
        "crypto": True,
    }


def get_kr_live_status(holdings: list) -> dict:
    price_map = get_latest_prices_map()
    market_status = get_market_status()
    results = []
    codes = [h["ticker_code"] for h in holdings]
    live_prices = {}
    if market_status["kr"] and codes:
        try:
            for code in codes:
                if re.match(r"^\d{6}$", code):
                    df = fdr.DataReader(code, datetime.now().strftime("%Y-%m-%d"))
                    if not df.empty:
                        live_prices[code] = float(df.iloc[-1]["Close"])
        except Exception as e:
            logger.error(f"국내 주식 실시간 수집 실패: {e}")
    for h in holdings:
        code = h["ticker_code"]
        qty = h["quantity"]
        curr_price = live_prices.get(code) or price_map.get(code, {}).get("close_price", 0.0)
        prev_price = price_map.get(code, {}).get("close_price", curr_price)
        eval_amt = qty * curr_price
        diff_amt = eval_amt - (qty * prev_price)
        diff_rate = (diff_amt / (qty * prev_price) * 100) if prev_price > 0 else 0.0
        results.append(
            {
                "ticker": code,
                "price": curr_price,
                "is_us": False,
                "eval_amount": eval_amt,
                "diff_amount": diff_amt,
                "diff_rate": diff_rate,
            }
        )
    return {"assets": results}


def get_us_live_status(holdings: list, fx_rate: float) -> dict:
    price_map = get_latest_prices_map()
    market_status = get_market_status()
    results = []
    codes = [h["ticker_code"] for h in holdings]
    live_prices = {}
    if market_status["us"] and codes:
        try:
            for code in codes:
                if code.isalpha():
                    df = fdr.DataReader(code, datetime.now().strftime("%Y-%m-%d"))
                    if not df.empty:
                        live_prices[code] = float(df.iloc[-1]["Close"])
        except Exception as e:
            logger.error(f"미국 주식 실시간 수집 실패: {e}")
    for h in holdings:
        code = h["ticker_code"]
        qty = h["quantity"]
        curr_price = live_prices.get(code) or price_map.get(code, {}).get("close_price", 0.0)
        prev_price = price_map.get(code, {}).get("close_price", curr_price)
        eval_amt_usd = qty * curr_price
        diff_amt_usd = (curr_price - prev_price) * qty
        diff_rate = ((curr_price - prev_price) / prev_price * 100) if prev_price > 0 else 0.0
        results.append(
            {
                "ticker": code,
                "price": curr_price,
                "is_us": True,
                "eval_amount": eval_amt_usd * fx_rate,
                "diff_amount": diff_amt_usd * fx_rate,
                "diff_rate": diff_rate,
            }
        )
    return {"assets": results}


def get_crypto_live_status(holdings: list) -> dict:
    price_map = get_latest_prices_map()
    results = []
    crypto_tickers = [h["ticker_code"] for h in holdings]
    live_prices = pyupbit.get_current_price(crypto_tickers) or {} if crypto_tickers else {}
    for h in holdings:
        code = h["ticker_code"]
        qty = h["quantity"]
        curr_price = live_prices.get(code) or price_map.get(code, {}).get("close_price", 0.0)
        prev_price = price_map.get(code, {}).get("close_price", curr_price)
        eval_amt = qty * curr_price
        diff_amt = eval_amt - (qty * prev_price)
        diff_rate = (diff_amt / (qty * prev_price) * 100) if prev_price > 0 else 0.0
        results.append(
            {
                "ticker": code.replace("KRW-", ""),
                "price": curr_price,
                "is_us": False,
                "eval_amount": eval_amt,
                "diff_amount": diff_amt,
                "diff_rate": diff_rate,
            }
        )
    return {"assets": results}


def get_live_tracker_status(asset_type: str = "all"):
    """
    멀티 자산 실시간 평가 엔진
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()
    if not holdings:
        return None

    fx_rate = get_current_fx_rate()

    # 1. 동일 종목 합산
    merged = defaultdict(lambda: {"ticker_code": "", "quantity": 0.0, "avg_price": 0.0})
    for h in holdings:
        code = h["ticker_code"]
        merged[code]["ticker_code"] = code
        merged[code]["quantity"] += h["quantity"]
        merged[code]["avg_price"] = h["avg_price"]

    holdings_list = list(merged.values())

    # 구분
    crypto_list = [h for h in holdings_list if h["ticker_code"].startswith("KRW-")]
    kr_list = [
        h
        for h in holdings_list
        if not h["ticker_code"].startswith("KRW-")
        and not h["ticker_code"].isalpha()
        and not h["ticker_code"].startswith(("K5", "KR5"))
    ]
    us_list = [h for h in holdings_list if h["ticker_code"].isalpha()]

    assets = []
    # 서브 함수 호출
    if asset_type in ["all", "crypto", "coin"]:
        assets.extend(get_crypto_live_status(crypto_list)["assets"])
    if asset_type in ["all", "kr", "stock"]:
        assets.extend(get_kr_live_status(kr_list)["assets"])
    if asset_type in ["all", "us"]:
        assets.extend(get_us_live_status(us_list, fx_rate)["assets"])

    # 집계 로직
    total_eval = sum(a.get("eval_amount", 0.0) for a in assets)
    total_diff = sum(a.get("diff_amount_krw", a.get("diff_amount", 0.0)) for a in assets)

    # 직전 평가액 역산 (0으로 나누기 방지)
    base_eval = total_eval - total_diff
    total_diff_rate = (total_diff / base_eval * 100) if base_eval > 0 else 0.0

    return {
        "timestamp": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "assets": assets,
        "total_eval": total_eval,
        "total_diff": total_diff,
        "total_diff_rate": total_diff_rate,
        "fx_rate": fx_rate,
    }


# 기존 함수는 라인 93에서 선언되었으며, 라인 180의 중복 선언을 삭제함.
# 라인 180-181 삭제
