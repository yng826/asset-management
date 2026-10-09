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
import re
import warnings
from collections import OrderedDict
from contextlib import suppress
from datetime import datetime, timedelta

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


# ----------------------------------------------------------------------
# 5. 계좌 유형 / 자산군 분류 및 예수금 (/status, /breakdown 공용)
# ----------------------------------------------------------------------
# 계좌 유형 표시 순서
ACCOUNT_TYPE_ORDER = ["일반 계좌", "연금·절세 계좌", "가상자산 계좌"]

# 자산군 분류 정규식 (database/schema.sql v_daily_asset_class_summary 와 동일 규칙)
_ETF_BRAND_PATTERN = re.compile(r"(TIGER|KODEX|ACE|SOL|RISE|KBSTAR|ARIRANG|PLUS|ETF)")
_GLOBAL_ETF_PATTERN = re.compile(
    r"(미국|S&P|나스닥|글로벌|차이나|인디아|필라델피아|SOXX|FANG|테크|빅테크|선진국|유로|니케이)"
)
CASH_ASSET_CLASS = "현금/예수금"


def classify_account_type(account_name: str) -> str:
    """계좌명으로 계좌 유형 판별 (일반 / 연금·절세 / 가상자산)."""
    name = str(account_name or "").upper()
    if "UPBIT" in name or "업비트" in name:
        return "가상자산 계좌"
    if any(k in name for k in ("IRP", "연금", "ISA")):
        return "연금·절세 계좌"
    return "일반 계좌"


def classify_asset_class(item: dict) -> str:
    """종목 1건의 자산군 판별 (schema.sql 자산군 뷰와 동일 규칙)."""
    code = str(item.get("ticker_code") or "")
    name = str(item.get("ticker_name") or "")
    if code.startswith("KRW-"):
        return "가상자산"
    if _ETF_BRAND_PATTERN.search(name):
        return "해외추종 ETF" if _GLOBAL_ETF_PATTERN.search(name) else "국내추종 ETF"
    if re.match(r"^[0-9]{6}$", code):
        return "국내 개별주"
    if re.match(r"^[A-Z]{1,5}$", code):
        return "해외주식"
    if (
        re.match(r"^(KR5|K55)", code)
        or code.startswith("4.42|")
        or item.get("price_source") == "deposit"
        or _deposit_v.is_deposit(name, code)
    ):
        return "펀드/퇴직예치"
    if "CASH" in code or code in ("KRW", "USD"):
        return CASH_ASSET_CLASS
    return "기타"


def get_account_cash_map(fx_rate: dict | None = None, target_date: str | None = None) -> dict:
    """계좌별 예수금(원화 환산) dict 반환 {account_name: krw_amount}.

    - 원장(DEPOSIT + SELL + DIVIDEND - BUY - WITHDRAW) 기반 순현금 (AssetRepository.get_account_cash_balances)
    - USD 예수금은 fx_rate(최신 수집 환율)로 원화 환산
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")
    if fx_rate is None:
        fx_rate = get_latest_fx_rate()
    krw_per_usd = float(fx_rate["rate"]) if fx_rate and "rate" in fx_rate else get_current_fx_rate()

    cash_map: dict = {}
    for row in AssetRepository().get_account_cash_balances(target_date):
        amount = row["net_cash"] * krw_per_usd if row["currency"] == "USD" else row["net_cash"]
        cash_map[row["account_name"]] = cash_map.get(row["account_name"], 0.0) + amount
    return cash_map


def get_closed_snapshot_date() -> str | None:
    """가장 최근 '결산 확정' 스냅샷 일자 (YYYY-MM-DD).

    - 당일 스냅샷은 16:00 일일 결산(closing_1600) 기록이 있을 때만 확정으로 간주
    - 그 전에는 어제 이전의 마지막 스냅샷일
    """
    repo = AssetRepository()
    today = datetime.now().strftime("%Y-%m-%d")
    for row in repo.get_daily_pnl_history():
        snapshot_date = str(row["snapshot_date"])[:10]
        if snapshot_date < today or repo.has_batch_run("closing_1600", today):
            return snapshot_date
    return None


def summarize_class_pnl(snapshot_date: str) -> dict:
    """결산일(snapshot_date)의 자산군별 손익 분해 {asset_class: {"pnl", "base"}}.

    - 계좌·종목별 손익 = (평가액 변화) - (투자원금 변화), 직전 스냅샷일 대비
      → 전 종목 합계가 /pnl 의 daily_pnl 과 일치 (예수금은 평가=원금이라 0)
    - base = 결산일 자산군 평가액 - 손익 (수익률 분모)
    """
    rows = AssetRepository().get_holding_snapshot_pair(snapshot_date)
    current: dict = {}
    previous: dict = {}
    for r in rows:
        key = (r["account_name"], r["ticker_code"])
        (current if r["snapshot_date"] == snapshot_date else previous)[key] = r

    result: dict = {}
    for key in set(current) | set(previous):
        cur, prev = current.get(key), previous.get(key)
        item = cur or prev
        name = classify_asset_class(item)
        cur_eval = cur["eval_amount"] if cur else 0.0
        cur_invested = cur["invested_amount"] if cur else 0.0
        prev_eval = prev["eval_amount"] if prev else 0.0
        prev_invested = prev["invested_amount"] if prev else 0.0
        pnl = (cur_eval - prev_eval) - (cur_invested - prev_invested)

        acc = result.setdefault(name, {"pnl": 0.0, "base": 0.0})
        acc["pnl"] += pnl
        acc["base"] += cur_eval - pnl
    return result


def summarize_asset_classes(
    enriched_holdings: list,
    cash_by_account: dict | None = None,
    class_pnl: dict | None = None,
) -> list:
    """자산군별 평가액·비중 + 결산일 손익 집계 (평가액 내림차순).

    - 평가액은 enrich_holdings_with_prices() 결과를 그대로 사용 (해외주식 환율·펀드 NAV 반영, /status 와 동일 기준)
    - class_pnl: summarize_class_pnl() 결과 (결산일 자산군별 손익). eval_diff / diff_pct 로 반영
    """
    class_pnl = class_pnl or {}

    classes: dict = {}
    for it in enriched_holdings:
        name = classify_asset_class(it)
        acc = classes.setdefault(name, {"asset_class": name, "class_eval": 0.0})
        acc["class_eval"] += float(it.get("valuation_amount") or 0.0)

    cash_total = sum((cash_by_account or {}).values())
    if cash_total:
        classes[CASH_ASSET_CLASS] = {"asset_class": CASH_ASSET_CLASS, "class_eval": cash_total}

    total_eval = sum(c["class_eval"] for c in classes.values())
    results = sorted(classes.values(), key=lambda c: c["class_eval"], reverse=True)
    for c in results:
        pnl_info = class_pnl.get(c["asset_class"], {"pnl": 0.0, "base": 0.0})
        c["eval_diff"] = pnl_info["pnl"]
        c["diff_pct"] = _safe_pnl_rate(pnl_info["pnl"], pnl_info["base"])
        c["weight_pct"] = (c["class_eval"] / total_eval * 100.0) if total_eval > 0 else 0.0
    return results


def save_snapshot_for_date(target_date: str) -> bool:
    """특정 날짜(target_date) 기준 총자산 및 종목별 세부 스냅샷 계산 및 저장"""
    repo = AssetRepository()
    holdings = repo.get_holdings_as_of_date(target_date)
    prices = get_prices_map_as_of_date(target_date)
    fx = get_fx_rate_as_of_date(target_date)

    # 가상자산: D 일봉 종가는 D+1 09:00 에야 확정되므로, D 결산(16:00)에는 D 09:00 에 확정된 D-1 종가 사용
    # (16:00 실시간 결산과 사후 재계산(repair/backfill) 결과를 일치시키기 위함)
    prev_day = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    crypto_prices = get_prices_map_as_of_date(prev_day)
    for code in [c for c in prices if str(c).startswith("KRW-")]:
        if code in crypto_prices:
            prices[code] = crypto_prices[code]
        else:
            del prices[code]

    enriched = enrich_holdings_with_prices(holdings, prices, fx)

    # 1. 통화별 실제 현금 잔액 산출
    cash_balances = repo.get_cash_balances_by_currency(target_date)
    krw_per_usd = float(fx["rate"]) if fx and "rate" in fx else get_current_fx_rate()

    cash_amount = 0.0
    for curr, bal in cash_balances.items():
        if curr == "USD":
            cash_amount += bal * krw_per_usd
        else:
            cash_amount += bal

    # 2. 비현금 자산 합산 (가상 CASH 티커로 인한 중복 합산 방지)
    non_cash_eval = sum(
        float(it.get("valuation_amount", 0.0))
        for it in enriched
        if "CASH" not in str(it.get("ticker_code", "")) and it.get("ticker_code") not in ("KRW", "USD")
    )
    non_cash_invested = sum(
        float(it.get("buy_amount", 0.0))
        for it in enriched
        if "CASH" not in str(it.get("ticker_code", "")) and it.get("ticker_code") not in ("KRW", "USD")
    )

    total_eval = non_cash_eval + cash_amount
    total_invested = non_cash_invested + cash_amount

    # 3. 일별 총자산 요약 스냅샷 저장
    snap_ok = repo.save_snapshot(
        target_date,
        {
            "total_eval_amount": total_eval,
            "total_invested_amount": total_invested,
            "cash_amount": cash_amount,
        },
    )

    # 4. 계좌·종목 단위 세부 스냅샷 저장 (일반 자산 + 계좌별 현금 예수금 항목 추가)
    holding_records = []
    for it in enriched:
        code = str(it.get("ticker_code", ""))
        if "CASH" in code or code in ("KRW", "USD"):
            continue
        qty = float(it.get("quantity") or 0.0)
        u_price = float(it.get("current_price") or it.get("close_price") or 0.0)
        calc_eval = it.get("valuation_amount") or it.get("eval_amount") or (qty * u_price)
        calc_invest = it.get("buy_amount") or it.get("invested_amount") or 0.0
        holding_records.append(
            {
                "snapshot_date": target_date,
                "account_name": it.get("account_name"),
                "ticker_code": code,
                "quantity": qty,
                "close_price": u_price,
                "eval_amount": float(calc_eval),
                "invested_amount": float(calc_invest),
            }
        )

    # 계좌별 현금 잔액을 holding_records에 추가 (v_daily_asset_class_summary 등 뷰 반영용)
    account_cash_list = repo.get_account_cash_balances(target_date)
    for acct_cash in account_cash_list:
        acc_name = acct_cash["account_name"]
        curr = acct_cash["currency"]
        net_cash = acct_cash["net_cash"]
        if net_cash == 0:
            continue
        ticker_code = "CASH_USD" if curr == "USD" else "CASH_KRW"
        eval_val = net_cash * krw_per_usd if curr == "USD" else net_cash
        close_p = krw_per_usd if curr == "USD" else 1.0

        holding_records.append(
            {
                "snapshot_date": target_date,
                "account_name": acc_name,
                "ticker_code": ticker_code,
                "quantity": 0.0,
                "close_price": close_p,
                "eval_amount": float(eval_val),
                "invested_amount": float(eval_val),
            }
        )

    repo.save_holding_snapshots(target_date, holding_records)
    return snap_ok


def refresh_recent_snapshots(days: int = 4) -> list:
    """최근 days 일(어제까지) 중 이미 저장된 결산 스냅샷을 최신 확정 시세로 재계산.

    - 16:00 결산 시점의 미확정 국내 종가 등이 다음 날 확정값으로 덮어써진 뒤 실행 (주말·연휴 감안 4일)
    - 사후 재계산(repair/backfill)과 같은 기준으로 맞춰짐
    Returns: 재계산한 일자 리스트
    """
    conn = get_connection()
    if not conn:
        return []
    today = datetime.now()
    start = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT snapshot_date FROM daily_snapshots WHERE snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date",
            (start, yesterday),
        )
        dates = [str(r[0]) for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()

    for d in dates:
        save_snapshot_for_date(d)
    return dates


def save_today_snapshot() -> bool:
    """당일 기준 총자산 및 종목별 세부 스냅샷 동시 저장"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    return save_snapshot_for_date(today_str)


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

    # 날짜 범위 생성 (포트폴리오·벤치마크 기준일 일치: 성과 측정 시작일 이전은 잘라냄)
    from core.performance import PERFORMANCE_INCEPTION_DATE

    start_date = max(start_date, PERFORMANCE_INCEPTION_DATE)
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")

    # 1. 포트폴리오 누적 수익률: TWR 기반 (입출금 효과 제거, 성과 측정 시작일 이전은 0)
    from core.performance import get_twr_series

    df_twr = get_twr_series(start_date, end_date)
    df_portfolio = pd.DataFrame({"return": df_twr["cum_return"]}).reindex(date_range).ffill()

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


def get_asset_stack_eval_history(start_date: str | None = None, end_date: str | None = None) -> dict:
    """
    v_daily_asset_class_summary 뷰에서 자산군별 일자별 평가액 조회 후 피벗 및 억 원 단위 변환.
    """
    if not start_date:
        start_date = "2026-05-01"
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    conn = get_connection()
    if not conn:
        return {"dates": [], "categories": [], "values": {}}

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
        return {"dates": [], "categories": [], "values": {}}

    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    date_range = pd.date_range(start=start_date, end=end_date, freq="D")

    df_pivot = df.pivot(index="snapshot_date", columns="asset_class", values="class_eval")
    df_pivot = df_pivot.reindex(date_range).ffill().fillna(0.0)

    # 억 원 단위 변환 (val / 100,000,000)
    df_values = df_pivot / 100_000_000.0

    ASSET_LABEL_MAP = {
        "해외추종 ETF": "Global ETF",
        "가상자산": "Crypto",
        "국내추종 ETF": "KR ETF",
        "국내 개별주": "KR Stock",
        "펀드/퇴직예치": "Fund/Pension",
        "해외주식": "US Stock",
        "현금/예수금": "Cash",
    }

    # rename columns using the map
    new_columns = [ASSET_LABEL_MAP.get(col, col) for col in df_values.columns]
    df_values.columns = new_columns
    df_values = df_values.T.groupby(level=0).sum().T

    dates = date_range.strftime("%Y-%m-%d").tolist()
    categories = df_values.columns.tolist()
    values = {cat: df_values[cat].tolist() for cat in categories}

    return {
        "dates": dates,
        "categories": categories,
        "values": values,
    }
