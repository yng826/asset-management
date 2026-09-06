"""
core/calculator.py
- 자산군별 평가 오케스트레이터 + DB 조회 + 집계.
- 실제 자산군별 평가 로직은 core/valuator/{deposit,fund,stock,crypto}.py 로 위임.

데이터 흐름:
    transactions (원장)
        → AssetRepository.get_current_holdings() → holdings list
        → DB 조회 (daily_prices, fx_rate) → price_map / fx_rate
        → enrich_holdings_with_prices()
              for each asset:
                for valuator in [deposit, fund, stock, crypto]:
                    result = valuator.valuation(asset, price_map, fx_rate)
                    if result: break
                if not result: fallback (avg_price → current_price)
        → enriched list
        → group / summarize (계좌별/전체)
"""

from datetime import datetime
from collections import OrderedDict
from contextlib import suppress

# 자산군별 valuator (lazy import 회피: 직접 import 하면 순환 위험 없음)
from core.valuator import (
    crypto as _crypto_v,
    deposit as _deposit_v,
    fund as _fund_v,
    stock as _stock_v,
)
from database.connection import get_connection
from database.repository import AssetRepository


# 표준 valuator 위임 순서 (우선순위). 매칭 안 되면 다음 valuator 시도.
# 매칭 우선순위: deposit > fund > stock (us_stock > market) > crypto > fallback
VALUATOR_CHAIN = [_deposit_v, _fund_v, _stock_v, _crypto_v]

# fallback 시 price_source 값
FALLBACK_SOURCE = "fallback"

# 환율 티커 식별자 (해외주식 KRW 환산용)
FX_USD_KRW = "USD/KRW"


# ----------------------------------------------------------------------
# 1. 공통 헬퍼
# ----------------------------------------------------------------------
def _safe_pnl_rate(profit: float, buy_amount: float) -> float:
    """수익률(%) 계산 헬퍼. 분모 0 보호."""
    if buy_amount <= 0:
        return 0.0
    return (profit / buy_amount) * 100.0


# ----------------------------------------------------------------------
# 2. DB 조회 (daily_prices / 환율)
# ----------------------------------------------------------------------
def get_latest_prices_map() -> dict:
    """daily_prices 테이블에서 ticker_code 별 최신 close_price dict 반환.

    - 동일 ticker_code 의 여러 price_date row 는 MAX(price_date) 1건만 반환
    - 환율 티커('USD/KRW')도 함께 반환
    """
    conn = get_connection()
    if not conn:
        return {}

    query = """
        SELECT t.ticker_code, t.price_date, t.close_price
        FROM daily_prices t
        INNER JOIN (
            SELECT ticker_code, MAX(price_date) AS max_date
            FROM daily_prices
            GROUP BY ticker_code
        ) latest
          ON t.ticker_code = latest.ticker_code
         AND t.price_date  = latest.max_date
    """
    try:
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ daily_prices 조회 실패: {e}")
        with suppress(Exception):
            conn.close()
        return {}

    price_map: dict = {}
    for ticker_code, price_date, close_price in rows:
        price_map[str(ticker_code)] = {
            "price_date": price_date,
            "close_price": float(close_price),
        }
    return price_map


def get_prices_map_as_of_date(target_date: str) -> dict:
    """target_date 기준 가장 최근 종가(price_date <= target_date) map 조회."""
    conn = get_connection()
    if not conn:
        return {}
    # SQLite/MySQL 호환을 위해 서브쿼리 활용 (각 종목별 target_date 이전의 max date)
    query = """
        SELECT t.ticker_code, t.price_date, t.close_price
        FROM daily_prices t
        INNER JOIN (
            SELECT ticker_code, MAX(price_date) AS max_date
            FROM daily_prices
            WHERE price_date <= ?
            GROUP BY ticker_code
        ) latest
          ON t.ticker_code = latest.ticker_code
         AND t.price_date  = latest.max_date
    """
    try:
        cur = conn.cursor()
        cur.execute(query, (target_date,))
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ daily_prices 조회 실패({target_date}): {e}")
        return {}
    return {r[0]: {"price_date": r[1], "close_price": float(r[2])} for r in rows}


def get_fx_rate_as_of_date(target_date: str, fx_ticker: str = FX_USD_KRW) -> dict | None:
    """target_date 이전 가장 최근 환율 조회."""
    conn = get_connection()
    if not conn:
        return None
    query = "SELECT price_date, close_price FROM daily_prices WHERE ticker_code = ? AND price_date <= ? ORDER BY price_date DESC LIMIT 1"
    try:
        cur = conn.cursor()
        cur.execute(query, (fx_ticker, target_date))
        row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception:
        return None
    return {"price_date": row[0], "rate": float(row[1])} if row else None


def get_latest_fx_rate(fx_ticker: str = FX_USD_KRW) -> dict | None:
    """가장 최신 USD/KRW 환율 1건 조회.

    Returns:
        {"price_date": date, "rate": float (KRW per USD)} 또는 None.
    """
    conn = get_connection()
    if not conn:
        return None
    query = """
        SELECT price_date, close_price FROM daily_prices
        WHERE ticker_code = ?
        ORDER BY price_date DESC LIMIT 1
    """
    try:
        cur = conn.cursor()
        cur.execute(query, (fx_ticker,))
        row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ 환율 조회 실패: {e}")
        with suppress(Exception):
            conn.close()
        return None
    if not row:
        return None
    return {"price_date": row[0], "rate": float(row[1])}


# ----------------------------------------------------------------------
# 3. 평가 오케스트레이터
# ----------------------------------------------------------------------
def _fallback_valuation(asset: dict) -> dict:
    """매칭되는 valuator 가 없을 때 avg_price 를 현재가로 사용하는 fallback.

    price_source = "fallback" (예수금/미수집 종목 등)
    """
    avg_price = float(asset.get("avg_price", 0.0) or 0.0)
    quantity = float(asset.get("quantity", 0.0) or 0.0)
    buy_amount = avg_price * quantity
    current_price = avg_price
    valuation_amount = current_price * quantity
    profit = valuation_amount - buy_amount
    pnl_rate = _safe_pnl_rate(profit, buy_amount)
    return {
        **asset,
        "current_price": current_price,
        "price_date": None,
        "price_source": FALLBACK_SOURCE,
        "buy_amount": buy_amount,
        "valuation_amount": valuation_amount,
        "profit": profit,
        "pnl_rate": pnl_rate,
    }


def enrich_holdings_with_prices(
    holdings: list,
    price_map: dict | None = None,
    fx_rate: dict | None = None,
) -> list:
    """holdings list 에 현재가/평가액/손익/수익률 필드를 더해 반환.

    우선순위 (각 valuator 순차 시도):
      1) 정기예금 (valuator.deposit)
      2) 펀드 (valuator.fund)
      3) 주식 (valuator.stock — 해외주식 KRW 환산 or 국내주식/ETF)
      4) 코인 (valuator.crypto)
      5) fallback (avg_price 사용, price_source='fallback')
    """
    if price_map is None:
        price_map = get_latest_prices_map()
    if fx_rate is None:
        fx_rate = get_latest_fx_rate()

    enriched: list = []
    for asset in holdings:
        result: dict | None = None
        for valuator in VALUATOR_CHAIN:
            print(f"🧮 평가 시도: {valuator.__name__} ({asset.get('ticker_code')}) {asset.get('quantity')}")
            result = valuator.valuation(asset, price_map, fx_rate)
            if result is not None:
                break
        if result is None:
            result = _fallback_valuation(asset)
        enriched.append(result)
    return enriched


# ----------------------------------------------------------------------
# 4. 집계 (계좌별 / 전체)
# ----------------------------------------------------------------------
def group_holdings_by_account(enriched_holdings: list) -> "OrderedDict[str, list]":
    """enriched holdings 를 account_name 기준으로 그룹화 (입력 순서 유지)."""
    grouped: OrderedDict[str, list] = OrderedDict()
    for h in enriched_holdings:
        account = h.get("account_name", "미분류")
        grouped.setdefault(account, []).append(h)
    return grouped


def summarize_accounts(grouped: "OrderedDict[str, list]") -> list:
    """계좌별 소계 dict 리스트 (입력 순서 유지).

    Returns:
        [{account_name, buy_amount, valuation_amount, profit, pnl_rate, count}, ...]
    """
    summaries: list = []
    for account, items in grouped.items():
        buy_amount = sum(it["buy_amount"] for it in items)
        valuation_amount = sum(it["valuation_amount"] for it in items)
        profit = valuation_amount - buy_amount
        summaries.append(
            {
                "account_name": account,
                "buy_amount": buy_amount,
                "valuation_amount": valuation_amount,
                "profit": profit,
                "pnl_rate": _safe_pnl_rate(profit, buy_amount),
                "count": len(items),
            }
        )
    return summaries


def summarize_total(enriched_holdings: list) -> dict:
    """전체 포트폴리오 합계 dict."""
    buy_amount = sum(it["buy_amount"] for it in enriched_holdings)
    valuation_amount = sum(it["valuation_amount"] for it in enriched_holdings)
    profit = valuation_amount - buy_amount
    return {
        "buy_amount": buy_amount,
        "valuation_amount": valuation_amount,
        "profit": profit,
        "pnl_rate": _safe_pnl_rate(profit, buy_amount),
        "count": len(enriched_holdings),
    }


def save_today_snapshot() -> bool:
    """당일 기준 총자산 스냅샷 저장"""
    repo = AssetRepository()
    holdings = repo.get_current_holdings()
    price_map = get_latest_prices_map()
    fx_rate = get_latest_fx_rate()
    enriched = enrich_holdings_with_prices(holdings, price_map, fx_rate)

    total_eval = sum(it["valuation_amount"] for it in enriched)
    total_invested = sum(it["buy_amount"] for it in enriched)
    # 캐시 자산은 ticker_code가 CASH_로 시작하는 것들 (간단 로직)
    cash_amount = sum(it["valuation_amount"] for it in enriched if "CASH" in it.get("ticker_code", ""))

    return repo.save_snapshot(
        datetime.now().strftime("%Y-%m-%d"),
        {
            "total_eval_amount": total_eval,
            "total_invested_amount": total_invested,
            "cash_amount": cash_amount,
        },
    )


# ----------------------------------------------------------------------
# 5. Backward-compat re-exports
#    (기존 import 호환: from core.calculator import is_deposit, is_fund_ticker, ...)
# ----------------------------------------------------------------------
is_deposit = _deposit_v.is_deposit
is_fund_ticker = _fund_v.is_fund_ticker
parse_deposit_metadata = _deposit_v.parse_deposit_metadata
calculate_deposit_valuation = _deposit_v.calculate_deposit_valuation
