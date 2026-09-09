import logging
import re
from collections import defaultdict
from datetime import datetime, time

import FinanceDataReader as fdr
import pytz
import pyupbit

from core.calculator import get_latest_prices_map
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


def get_live_tracker_status(asset_type: str = "all"):
    """
    멀티 자산 실시간 평가 엔진
    asset_type: 'all', 'crypto', 'kr', 'us'
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()
    if not holdings:
        return None

    price_map = get_latest_prices_map()
    fx_rate = price_map.get("USD/KRW", {}).get("close_price", 1350.0)
    market_status = get_market_status()

    # 1. 동일 종목 합산
    merged_holdings = defaultdict(lambda: {"ticker_code": "", "quantity": 0.0, "avg_price": 0.0})
    for h in holdings:
        code = h["ticker_code"]
        merged_holdings[code]["ticker_code"] = code
        merged_holdings[code]["quantity"] += h["quantity"]
        merged_holdings[code]["avg_price"] = h["avg_price"]

    holdings_list = list(merged_holdings.values())

    # 필터링
    if asset_type == "crypto":
        holdings_list = [h for h in holdings_list if h["ticker_code"].startswith("KRW-")]
    elif asset_type == "kr":
        holdings_list = [
            h
            for h in holdings_list
            if not h["ticker_code"].startswith("KRW-")
            and not h["ticker_code"].isalpha()
            and not h["ticker_code"].startswith(("K5", "KR5"))
        ]
    elif asset_type == "us":
        holdings_list = [h for h in holdings_list if h["ticker_code"].isalpha()]

    live_prices = {}

    # Crypto 실시간
    crypto_tickers = [h["ticker_code"] for h in holdings_list if h["ticker_code"].startswith("KRW-")]
    if market_status["crypto"] and crypto_tickers:
        try:
            live_prices.update(pyupbit.get_current_price(crypto_tickers) or {})
        except Exception as e:
            logger.error(f"Crypto 실시간 수집 실패: {e}")

    # KR/US 실시간 (펀드 제외)
    targets = [
        h["ticker_code"]
        for h in holdings_list
        if re.match(r"^(\d{6}|[A-Z]{1,5})$", h["ticker_code"])
        and h["ticker_code"] not in ("KRW", "USD", "CASH_KRW")
    ]
    if (market_status["kr"] or market_status["us"]) and targets:
        try:
            for code in targets:
                df = fdr.DataReader(code, datetime.now().strftime("%Y-%m-%d"))
                if not df.empty:
                    live_prices[code] = float(df.iloc[-1]["Close"])
        except Exception as e:
            logger.error(f"주식 실시간 수집 실패: {e}")

    results = []
    total_eval = 0.0
    total_prev_eval = 0.0

    for h in holdings_list:
        code = h["ticker_code"]
        qty = h["quantity"]
        is_us = code.isalpha()
        is_fund = code.startswith(("K5", "KR5"))

        curr_price = live_prices.get(code)
        if curr_price is None:
            curr_price = price_map.get(code, {}).get("close_price", 0.0)

        prev_price = price_map.get(code, {}).get("close_price", curr_price)

        if curr_price <= 0 and prev_price <= 0:
            continue

        if is_fund:
            eval_amt = (qty * curr_price) / 1000
            prev_eval_amt = (qty * prev_price) / 1000
        else:
            eval_amt = qty * curr_price
            prev_eval_amt = qty * prev_price

        if is_us:
            eval_amt *= fx_rate
            prev_eval_amt *= fx_rate

        total_eval += eval_amt
        total_prev_eval += prev_eval_amt

        # 펀드는 실시간 화면 리스트에서 제외 (단, total_eval 합산은 위에서 이미 완료)
        if is_fund:
            continue

        if is_us:
            diff_amt_item = (curr_price - prev_price) * qty
        else:
            diff_amt_item = eval_amt - prev_eval_amt

        diff_rate = (diff_amt_item / prev_eval_amt * 100) if prev_eval_amt > 0 else 0

        results.append(
            {
                "ticker": code.replace("KRW-", ""),
                "price": curr_price,
                "is_us": is_us,
                "eval_amount": eval_amt,
                "diff_amount": diff_amt_item,
                "diff_rate": diff_rate,
            }
        )

    total_diff = total_eval - total_prev_eval
    total_diff_rate = (total_diff / total_prev_eval * 100) if total_prev_eval > 0 else 0

    return {
        "timestamp": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "assets": results,
        "total_eval": total_eval,
        "total_diff": total_diff,
        "total_diff_rate": total_diff_rate,
        "fx_rate": fx_rate,
    }


def get_crypto_live_status():
    return get_live_tracker_status(asset_type="crypto")
