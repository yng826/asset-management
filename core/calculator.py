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

import logging
import warnings
from collections import OrderedDict
from contextlib import suppress
from datetime import datetime

import pandas as pd

from core.valuator import (
    crypto as _crypto_v,
    deposit as _deposit_v,
    fund as _fund_v,
    stock as _stock_v,
)
from database.connection import get_connection
from database.repository import AssetRepository

logger = logging.getLogger(__name__)

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


def get_current_fx_rate(fx_ticker: str = FX_USD_KRW, default: float = 1350.0) -> float:
    """순수 환율 수치(float)만 반환하는 계산 전용 헬퍼."""
    fx_info = get_latest_fx_rate(fx_ticker)
    return float(fx_info["rate"]) if fx_info and "rate" in fx_info else default


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
            logger.debug(
                f"🧮 평가 시도: {valuator.__name__} ({asset.get('ticker_code')}) {asset.get('quantity')}"
            )
            result = valuator.valuation(asset, price_map, fx_rate)
            if result is not None:
                break
        if result is None:
            result = _fallback_valuation(asset)
        enriched.append(result)
    return enriched


def get_single_asset_performance(
    ticker_code: str, holdings: list, price_map: dict, fx_rate: dict | None
) -> dict | None:
    """특정 종목의 보유 정보와 시세를 결합하여 수익률/평가액 계산."""
    # 1. 해당 자산 찾기
    asset = next((h for h in holdings if h.get("ticker_code") == ticker_code), None)
    if not asset:
        return None

    # 2. 평가 수행 (VALUATOR_CHAIN 활용)
    result = None
    for valuator in VALUATOR_CHAIN:
        result = valuator.valuation(asset, price_map, fx_rate)
        if result is not None:
            break

    # 3. 매칭 실패 시 fallback 처리
    if result is None:
        result = _fallback_valuation(asset)

    return result


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
    """당일 기준 총자산 및 종목별 세부 스냅샷 동시 저장"""
    repo = AssetRepository()
    holdings = repo.get_current_holdings()
    price_map = get_latest_prices_map()
    fx_rate = get_latest_fx_rate()
    enriched = enrich_holdings_with_prices(holdings, price_map, fx_rate)

    total_eval = sum(it.get("valuation_amount", 0) for it in enriched)
    total_invested = sum(it.get("buy_amount", 0) for it in enriched)
    cash_amount = sum(it.get("valuation_amount", 0) for it in enriched if "CASH" in it.get("ticker_code", ""))

    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. 일별 총자산 요약 스냅샷 저장
    snap_ok = repo.save_snapshot(
        today_str,
        {
            "total_eval_amount": total_eval,
            "total_invested_amount": total_invested,
            "cash_amount": cash_amount,
        },
    )

    # 2. 계좌·종목 단위 세부 스냅샷 저장 (정확한 키 매핑)
    holding_records = []
    for it in enriched:
        qty = float(it.get("quantity") or 0.0)
        u_price = float(it.get("current_price") or it.get("close_price") or 0.0)
        calc_eval = it.get("valuation_amount") or it.get("eval_amount") or (qty * u_price)
        calc_invest = it.get("buy_amount") or it.get("invested_amount") or 0.0
        holding_records.append(
            {
                "snapshot_date": today_str,
                "account_name": it.get("account_name"),
                "ticker_code": it.get("ticker_code"),
                "quantity": qty,
                # current_price 와 valuation_amount 키 확인
                "close_price": u_price,
                "eval_amount": float(calc_eval),
                "invested_amount": float(calc_invest),
            }
        )

    repo.save_holding_snapshots(today_str, holding_records)
    return snap_ok


# ----------------------------------------------------------------------
# 5. Backward-compat re-exports
#    (기존 import 호환: from core.calculator import is_deposit, is_fund_ticker, ...)
# ----------------------------------------------------------------------
is_deposit = _deposit_v.is_deposit
is_fund_ticker = _fund_v.is_fund_ticker
parse_deposit_metadata = _deposit_v.parse_deposit_metadata
calculate_deposit_valuation = _deposit_v.calculate_deposit_valuation


def get_performance_comparison(
    start_date: str,
    end_date: str,
    benchmark_tickers: list[str] = None,
) -> dict:
    """
    내 포트폴리오 수익률과 벤치마크 지수 수익률 비교 데이터 생성.
    """
    if benchmark_tickers is None:
        benchmark_tickers = ["KS11", "US500"]

    # 날짜 범위 생성
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")

    # 1. 포트폴리오 스냅샷 조회
    conn = get_connection()
    portfolio_query = """
        SELECT snapshot_date, total_eval_amount, total_invested_amount
        FROM daily_snapshots
        WHERE snapshot_date BETWEEN ? AND ?
        ORDER BY snapshot_date
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        df_portfolio = pd.read_sql_query(portfolio_query, conn, params=(start_date, end_date))
    conn.close()

    df_portfolio["snapshot_date"] = pd.to_datetime(df_portfolio["snapshot_date"])
    df_portfolio = df_portfolio.set_index("snapshot_date").reindex(date_range).ffill()

    # 수익률 계산: (eval / initial_eval) - 1. 단, 원금 변동 고려 시 정교화 필요.
    # 우선 단순 평가액 기반 누적 수익률로 구현 (실무 상세 로직 적용 예정)
    initial_eval = df_portfolio["total_eval_amount"].iloc[0]
    # NOTE(Hyuk): 원금 변동(total_invested_amount) 발생 시, 단순히
    # 평가액/시작평가액 비율을 사용하면 입출금에 의한 왜곡이 발생함.
    # 향후 TWR(Time-Weighted Return) 방식 도입 시 이 로직을 개선할 것.

    df_portfolio["return"] = (df_portfolio["total_eval_amount"] / initial_eval - 1) * 100

    # 2. 벤치마크 지수 조회
    benchmarks = {}
    conn = get_connection()
    for ticker in benchmark_tickers:
        bm_query = "SELECT price_date, close_price FROM daily_prices WHERE ticker_code = ? AND price_date BETWEEN ? AND ? ORDER BY price_date"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            df_bm = pd.read_sql_query(bm_query, conn, params=(ticker, start_date, end_date))
        if df_bm.empty:
            benchmarks[ticker] = [0.0] * len(date_range)
            continue

        df_bm["price_date"] = pd.to_datetime(df_bm["price_date"])
        df_bm = df_bm.set_index("price_date").reindex(date_range).ffill()

        initial_price = df_bm["close_price"].iloc[0]
        # 시작점 데이터가 없는 경우 0.0 처리 혹은 보간된 첫 값 사용
        if pd.isna(initial_price):
            initial_price = (
                df_bm["close_price"].dropna().iloc[0] if not df_bm["close_price"].dropna().empty else 1.0
            )

        df_bm["return"] = (df_bm["close_price"] / initial_price - 1) * 100
        benchmarks[ticker] = df_bm["return"].fillna(0.0).tolist()
    conn.close()

    return {
        "dates": date_range.strftime("%Y-%m-%d").tolist(),
        "portfolio": df_portfolio["return"].fillna(0.0).tolist(),
        "benchmarks": benchmarks,
    }


def get_asset_allocation_history(start_date: str | None = None, end_date: str | None = None) -> dict:
    """
    v_daily_asset_class_summary 뷰에서 자산군별 일자별 평가액 조회 후 피벗 및 비중(%) 환산.
    """
    if not start_date:
        start_date = "2026-05-01"
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    if not conn:
        return {"dates": [], "categories": [], "weights": {}}

    query = """
        SELECT snapshot_date, asset_class, class_eval
        FROM v_daily_asset_class_summary
        WHERE snapshot_date BETWEEN ? AND ?
        ORDER BY snapshot_date
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        df = pd.read_sql_query(query, conn, params=(start_date, end_date))
    conn.close()

    if df.empty:
        return {"dates": [], "categories": [], "weights": {}}

    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")

    df_pivot = df.pivot(index="snapshot_date", columns="asset_class", values="class_eval")
    df_pivot = df_pivot.reindex(date_range).ffill().fillna(0.0)

    # 일자별 합계 대비 100% 비중(%) 환산
    row_sums = df_pivot.sum(axis=1)
    df_weights = df_pivot.div(row_sums.replace(0, 1), axis=0) * 100.0

    ASSET_LABEL_MAP = {
        "해외추종 ETF": "Global ETF",
        "가상자산": "Crypto",
        "국내추종 ETF": "KR ETF",
        "국내 개별주": "KR Stock",
        "펀드/퇴직예치": "Fund/Pension",
        "해외주식": "US Stock",
        "현금/예수금": "Cash",
    }

    new_columns = [ASSET_LABEL_MAP.get(col, "Other") for col in df_weights.columns]
    df_weights.columns = new_columns
    df_weights = df_weights.T.groupby(level=0).sum().T

    dates = date_range.strftime("%Y-%m-%d").tolist()
    categories = df_weights.columns.tolist()
    weights = {cat: df_weights[cat].tolist() for cat in categories}

    return {
        "dates": dates,
        "categories": categories,
        "weights": weights,
    }
