import logging
from datetime import datetime

import pyupbit

from core.calculator import get_latest_prices_map
from database.repository import AssetRepository

logger = logging.getLogger(__name__)


def get_crypto_live_status():
    """
    가상자산 실시간 평가 로직
    - 보유 중인 가상자산 조회
    - 현재가(pyupbit) 및 전일 종가(daily_prices) 비교
    """
    repo = AssetRepository()
    holdings = [h for h in repo.get_current_holdings() if h["ticker_code"].startswith("KRW-")]

    if not holdings:
        return None

    tickers = [h["ticker_code"] for h in holdings]

    # 1. 실시간 가격 조회
    try:
        current_prices = pyupbit.get_current_price(tickers)
        if not isinstance(current_prices, dict):
            current_prices = {tickers[0]: current_prices}
    except Exception as e:
        logger.error(f"실시간 가격 조회 실패: {e}")
        return None

    # 2. 전일 종가 조회
    price_map = get_latest_prices_map()

    results = []
    total_eval = 0.0
    total_prev_eval = 0.0

    for h in holdings:
        code = h["ticker_code"]
        qty = h["quantity"]
        curr_price = current_prices.get(code)
        prev_price = price_map.get(code, {}).get("close_price", curr_price)

        if curr_price is None:
            continue

        eval_amt = qty * curr_price
        prev_eval_amt = qty * prev_price

        diff_amt = eval_amt - prev_eval_amt
        diff_rate = (diff_amt / prev_eval_amt * 100) if prev_eval_amt > 0 else 0

        total_eval += eval_amt
        total_prev_eval += prev_eval_amt

        results.append(
            {
                "ticker": code.replace("KRW-", ""),
                "price": curr_price,
                "eval_amount": eval_amt,
                "diff_amount": diff_amt,
                "diff_rate": diff_rate,
            }
        )

    total_diff = total_eval - total_prev_eval
    total_diff_rate = (total_diff / total_prev_eval * 100) if total_prev_eval > 0 else 0

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "assets": results,
        "total_eval": total_eval,
        "total_diff": total_diff,
        "total_diff_rate": total_diff_rate,
    }
