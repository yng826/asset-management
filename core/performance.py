"""
core/performance.py
- 성과 분석 공통 기반: TWR(시간가중수익률) 일간 수익률 시계열.
- 수익률 비교·낙폭·월별 손익·리스크 지표 차트가 이 시계열을 공유한다.

계산 규칙:
    일간 수익률 r_d = (V_d - F_d) / V_{d-1} - 1
        V_d : daily_snapshots.total_eval_amount (당일 결산 평가액, 원화)
        F_d : 당일 순입금 (DEPOSIT - WITHDRAW, USD는 당일 이전 최신 환율로 원화 환산)
    누적지수 I_d = Π(1 + r) (시작일 = 1.0)
    - 입출금은 당일 종가 이후 반영된 것으로 간주 (결산 스냅샷에 이미 포함)
    - 배당은 외부 유입이 아닌 투자 수익이므로 F_d에 포함하지 않음
"""

import math
import warnings

import pandas as pd

from database.connection import get_connection

# 성과 측정 시작일: 전 계좌 초기잔고가 원장에 등록된 날.
# 이전 스냅샷은 가상자산만 존재하고, 당일 초기잔고 입금은 원가 기준이라 수익률 계산에서 제외한다.
PERFORMANCE_INCEPTION_DATE = "2026-05-01"

FX_USD_KRW = "USD/KRW"

# 샤프 지수 무위험수익률 (연, 국내 단기금리 수준)
RISK_FREE_RATE = 0.025

# 연환산 기간 수: 포트폴리오 스냅샷은 주말 포함 매일(가상자산), 지수는 거래일만 존재
PORTFOLIO_PERIODS_PER_YEAR = 365
BENCHMARK_PERIODS_PER_YEAR = 252


def _read_sql(query: str, params: tuple) -> pd.DataFrame:
    conn = get_connection()
    if not conn:
        return pd.DataFrame()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def get_daily_net_flows(start_date: str, end_date: str) -> pd.Series:
    """일자별 순입금액(원화) 시리즈. 입금 +, 출금 -. 거래가 없는 날은 포함되지 않음."""
    df = _read_sql(
        """
        SELECT trans_date, action_type, currency, total_amount
        FROM transactions
        WHERE action_type IN ('DEPOSIT', 'WITHDRAW') AND trans_date BETWEEN ? AND ?
        """,
        (start_date, end_date),
    )
    if df.empty:
        return pd.Series(dtype=float)

    df["trans_date"] = pd.to_datetime(df["trans_date"])
    df["amount"] = df["total_amount"].astype(float)
    df.loc[df["action_type"] == "WITHDRAW", "amount"] *= -1

    usd = df["currency"].fillna("KRW").str.upper() == "USD"
    if usd.any():
        # 거래일 이전 최신 환율 (주말·휴일은 직전 영업일 환율)
        fx = _read_sql(
            "SELECT price_date, close_price FROM daily_prices WHERE ticker_code = ? AND price_date <= ? ORDER BY price_date",
            (FX_USD_KRW, end_date),
        )
        fx["price_date"] = pd.to_datetime(fx["price_date"])
        fx_series = fx.set_index("price_date")["close_price"].astype(float)
        rates = fx_series.reindex(fx_series.index.union(df.loc[usd, "trans_date"].unique())).ffill()
        df.loc[usd, "amount"] *= df.loc[usd, "trans_date"].map(rates).to_numpy()

    return df.groupby("trans_date")["amount"].sum()


def get_twr_series(start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """
    TWR 일간 시계열.
    Returns: index=일자(DatetimeIndex) DataFrame
        eval_amount  : 결산 평가액
        net_flow     : 당일 순입금 (원화)
        daily_return : 일간 수익률 (소수, 첫 행 0)
        index        : 누적지수 (첫 행 1.0)
        cum_return   : 누적 수익률 (%)
    시작일이 성과 측정 시작일보다 이르면 측정 시작일로 맞춤. 데이터가 없으면 빈 DataFrame.
    """
    start_date = max(start_date or PERFORMANCE_INCEPTION_DATE, PERFORMANCE_INCEPTION_DATE)
    end_date = end_date or pd.Timestamp.now().strftime("%Y-%m-%d")

    df = _read_sql(
        "SELECT snapshot_date, total_eval_amount FROM daily_snapshots WHERE snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date",
        (start_date, end_date),
    )
    if df.empty:
        return pd.DataFrame(columns=["eval_amount", "net_flow", "daily_return", "index", "cum_return"])

    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    df = df.set_index("snapshot_date").rename(columns={"total_eval_amount": "eval_amount"})
    df["eval_amount"] = df["eval_amount"].astype(float)

    # 스냅샷 누락일의 입출금은 다음 스냅샷 일자로 이월
    flows = get_daily_net_flows(start_date, end_date)
    flows = flows[flows.index <= df.index[-1]] if not flows.empty else flows
    if flows.empty:
        df["net_flow"] = 0.0
    else:
        bucket = df.index[df.index.searchsorted(flows.index)]
        df["net_flow"] = flows.groupby(bucket).sum().reindex(df.index, fill_value=0.0)

    prev = df["eval_amount"].shift(1)
    df["daily_return"] = ((df["eval_amount"] - df["net_flow"]) / prev - 1).where(prev > 0, 0.0)
    df.iloc[0, df.columns.get_loc("daily_return")] = 0.0  # 시작일은 기준점
    df["index"] = (1 + df["daily_return"]).cumprod()
    df["cum_return"] = (df["index"] - 1) * 100
    return df


def compute_risk_metrics(
    index: pd.Series, periods_per_year: int, risk_free_rate: float = RISK_FREE_RATE
) -> dict:
    """
    누적지수(또는 가격) 시리즈로 리스크 지표 계산.
    Returns:
        period_return : 기간 수익률 (%)
        volatility    : 연환산 변동성 (%)
        sharpe        : 샤프 지수 (일간 초과수익 평균 / 표준편차 × √연환산, 데이터 부족 시 None)
        mdd           : 최대 낙폭 (%, 음수)
        peak_date / trough_date / recovery_date : MDD 고점·저점·회복일 (미회복 시 recovery_date None)
        drawdown      : 고점 대비 하락률 시리즈 (%)
    """
    index = index.dropna().astype(float)
    if len(index) < 2:
        return {}

    returns = index.pct_change().dropna()
    std = returns.std()
    volatility = std * math.sqrt(periods_per_year) * 100
    sharpe = None
    if len(returns) >= 2 and std > 0:
        excess = returns.mean() - risk_free_rate / periods_per_year
        sharpe = excess / std * math.sqrt(periods_per_year)

    drawdown = (index / index.cummax() - 1) * 100
    trough_date = drawdown.idxmin()
    peak_date = index.loc[:trough_date].idxmax()
    recovered = index.loc[trough_date:][index.loc[trough_date:] >= index.loc[peak_date]]
    recovery_date = recovered.index[0] if drawdown.min() < 0 and not recovered.empty else None

    return {
        "period_return": (index.iloc[-1] / index.iloc[0] - 1) * 100,
        "volatility": volatility,
        "sharpe": sharpe,
        "mdd": drawdown.min(),
        "peak_date": peak_date,
        "trough_date": trough_date,
        "recovery_date": recovery_date,
        "drawdown": drawdown,
    }


def get_drawdown_report(
    start_date: str | None = None,
    end_date: str | None = None,
    benchmark_tickers: list[str] | None = None,
) -> dict:
    """
    낙폭 차트·리스크 지표 데이터 (포트폴리오 TWR + 벤치마크 지수).
    Returns:
        dates      : 일자 문자열 리스트 (포트폴리오 스냅샷 기준)
        portfolio  : {"cum_return": [...], "drawdown": [...], "metrics": {...}}
        benchmarks : {ticker: {"drawdown": [...] (dates 정렬, 휴장일은 직전값), "metrics": {...}}}
    데이터가 없으면 dates 빈 리스트.
    """
    if benchmark_tickers is None:
        benchmark_tickers = ["KS11", "US500"]

    df = get_twr_series(start_date, end_date)
    if len(df) < 2:
        return {"dates": [], "portfolio": {}, "benchmarks": {}}

    p_metrics = compute_risk_metrics(df["index"], PORTFOLIO_PERIODS_PER_YEAR)
    result = {
        "dates": df.index.strftime("%Y-%m-%d").tolist(),
        "portfolio": {
            "cum_return": df["cum_return"].tolist(),
            "drawdown": p_metrics.pop("drawdown").tolist(),
            "metrics": p_metrics,
        },
        "benchmarks": {},
    }

    # 벤치마크는 포트폴리오와 같은 구간 (시작일 직전 거래일 종가를 기준점으로 포함)
    first, last = df.index[0].strftime("%Y-%m-%d"), df.index[-1].strftime("%Y-%m-%d")
    for ticker in benchmark_tickers:
        bm = _read_sql(
            """
            SELECT price_date, close_price FROM daily_prices
            WHERE ticker_code = ? AND price_date BETWEEN
                COALESCE((SELECT MAX(price_date) FROM daily_prices WHERE ticker_code = ? AND price_date <= ?), ?) AND ?
            ORDER BY price_date
            """,
            (ticker, ticker, first, first, last),
        )
        if len(bm) < 2:
            continue
        bm["price_date"] = pd.to_datetime(bm["price_date"])
        prices = bm.set_index("price_date")["close_price"].astype(float)
        metrics = compute_risk_metrics(prices, BENCHMARK_PERIODS_PER_YEAR)
        drawdown = metrics.pop("drawdown")
        aligned = drawdown.reindex(drawdown.index.union(df.index)).ffill().reindex(df.index).fillna(0.0)
        result["benchmarks"][ticker] = {"drawdown": aligned.tolist(), "metrics": metrics}

    return result


def get_monthly_pnl(start_date: str | None = None, end_date: str | None = None) -> dict:
    """
    월별 투자손익 (입출금 제외) 및 월간 TWR 수익률.
    - 시작일은 해당 월 1일로 맞춤 (전월 말 평가액을 기준점으로 사용, 성과 측정 시작일 이전은 제외)
    - 월 손익 = Σ(당일 평가액 − 전일 평가액 − 당일 순입금), 배당은 손익에 포함
    Returns:
        months     : "YYYY-MM" 리스트
        profit     : 월 손익 (원)
        returns    : 월간 수익률 (%)
        cum_profit : 누적 손익 (원)
        partial    : 진행 중(종료일이 월말 전)인 월 여부 리스트
    데이터가 없으면 months 빈 리스트.
    """
    end_ts = pd.Timestamp(end_date) if end_date else pd.Timestamp.now().normalize()
    month_start = (
        pd.Timestamp(start_date) if start_date else pd.Timestamp(PERFORMANCE_INCEPTION_DATE)
    ).replace(day=1)
    base_date = (month_start - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    df = get_twr_series(base_date, end_ts.strftime("%Y-%m-%d"))
    if len(df) < 2:
        return {"months": [], "profit": [], "returns": [], "cum_profit": [], "partial": []}

    df["profit"] = df["eval_amount"].diff() - df["net_flow"]
    df = df.iloc[1:]  # 첫 행은 기준점
    df = df[df.index >= month_start]

    month_key = df.index.to_period("M")
    monthly = df.groupby(month_key).agg(
        profit=("profit", "sum"),
        growth=("daily_return", lambda r: (1 + r).prod() - 1),
        last_day=("eval_amount", lambda s: s.index.max()),
    )
    partial = [
        last < period.end_time.normalize()
        for period, last in zip(monthly.index, monthly["last_day"], strict=True)
    ]
    return {
        "months": [str(p) for p in monthly.index],
        "profit": monthly["profit"].tolist(),
        "returns": (monthly["growth"] * 100).tolist(),
        "cum_profit": monthly["profit"].cumsum().tolist(),
        "partial": partial,
    }


CASH_TICKER_PREFIX = "CASH_"


def _to_krw(df: pd.DataFrame, date_col: str, amount_col: str, end_date: str) -> pd.Series:
    """USD 통화 행의 금액을 거래일 이전 최신 USD/KRW 환율로 원화 환산한 시리즈 반환."""
    amounts = df[amount_col].astype(float).copy()
    usd = df["currency"].fillna("KRW").str.upper() == "USD"
    if usd.any():
        fx = _read_sql(
            "SELECT price_date, close_price FROM daily_prices WHERE ticker_code = ? AND price_date <= ? ORDER BY price_date",
            (FX_USD_KRW, end_date),
        )
        fx["price_date"] = pd.to_datetime(fx["price_date"])
        fx_series = fx.set_index("price_date")["close_price"].astype(float)
        rates = fx_series.reindex(fx_series.index.union(df.loc[usd, date_col].unique())).ffill()
        amounts[usd] *= df.loc[usd, date_col].map(rates).to_numpy()
    return amounts


def get_ticker_contribution(start_date: str | None = None, end_date: str | None = None) -> dict:
    """
    기간 손익의 종목별 기여도 (실현손익·배당 포함, 계좌 합산).
    종목 기여 = 종료 평가액 − 시작 평가액 − 기간 매수액 + 기간 매도액 + 기간 배당
        - 시작 평가액은 시작일 전일 스냅샷 기준 (월별 손익과 같은 기준점)
        - USD 거래는 거래일 환율로 원화 환산
    현금·예수금(환율 변동, 이자, 원장 보정 등)은 포트폴리오 전체 손익과의 차액으로 계산해 합계가 일치하도록 함.
    Returns:
        start / end   : 실제 기준 일자 (시작 기준점 다음 날 ~ 종료 스냅샷)
        total_profit  : 기간 전체 손익 (원)
        base_eval     : 시작 기준 평가액 (원)
        items         : [{"ticker", "name", "profit", "realized"}] (손익 내림차순, 현금은 ticker "CASH")
    데이터가 없으면 items 빈 리스트.
    """
    end_ts = pd.Timestamp(end_date) if end_date else pd.Timestamp.now().normalize()
    start_ts = pd.Timestamp(start_date) if start_date else pd.Timestamp(PERFORMANCE_INCEPTION_DATE)
    base_date = (start_ts - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    twr = get_twr_series(base_date, end_ts.strftime("%Y-%m-%d"))
    empty = {"start": None, "end": None, "total_profit": 0.0, "base_eval": 0.0, "items": []}
    if len(twr) < 2:
        return empty

    base_day, end_day = twr.index[0].strftime("%Y-%m-%d"), twr.index[-1].strftime("%Y-%m-%d")
    total_profit = float(
        twr["eval_amount"].iloc[-1] - twr["eval_amount"].iloc[0] - twr["net_flow"].iloc[1:].sum()
    )

    holdings = _read_sql(
        """
        SELECT snapshot_date, ticker_code, SUM(eval_amount) AS eval_amount
        FROM daily_holding_snapshots
        WHERE snapshot_date IN (?, ?) AND ticker_code NOT LIKE 'CASH\\_%'
        GROUP BY snapshot_date, ticker_code
        """,
        (base_day, end_day),
    )
    holdings["snapshot_date"] = holdings["snapshot_date"].astype(str)
    holdings["eval_amount"] = holdings["eval_amount"].astype(float)
    evals = holdings.pivot_table(
        index="ticker_code", columns="snapshot_date", values="eval_amount", aggfunc="sum"
    )
    start_eval = evals[base_day] if base_day in evals else pd.Series(dtype=float)
    end_eval = evals[end_day] if end_day in evals else pd.Series(dtype=float)

    trades = _read_sql(
        """
        SELECT trans_date, ticker_code, action_type, total_amount, currency
        FROM transactions
        WHERE action_type IN ('BUY', 'SELL', 'DIVIDEND') AND trans_date > ? AND trans_date <= ?
            AND ticker_code IS NOT NULL AND ticker_code NOT LIKE 'CASH\\_%'
        """,
        (base_day, end_day),
    )
    if not trades.empty:
        trades["trans_date"] = pd.to_datetime(trades["trans_date"])
        trades["krw"] = _to_krw(trades, "trans_date", "total_amount", end_day)
        sign = trades["action_type"].map({"BUY": -1.0, "SELL": 1.0, "DIVIDEND": 1.0})
        cash_flow = (trades["krw"] * sign).groupby(trades["ticker_code"]).sum()
        realized = (
            trades.loc[trades["action_type"] != "BUY"].groupby("ticker_code")["krw"].sum()
        )  # 매도·배당 유입액
    else:
        cash_flow = realized = pd.Series(dtype=float)

    tickers = start_eval.index.union(end_eval.index).union(cash_flow.index)
    profit = (
        end_eval.reindex(tickers).fillna(0.0)
        - start_eval.reindex(tickers).fillna(0.0)
        + cash_flow.reindex(tickers).fillna(0.0)
    )

    names = _read_sql(
        """
        SELECT t.ticker_code, t.ticker_name FROM transactions t
        JOIN (SELECT ticker_code, MAX(id) AS id FROM transactions WHERE ticker_code IS NOT NULL GROUP BY ticker_code) m
            ON t.id = m.id
        """,
        (),
    )
    name_map = dict(zip(names["ticker_code"], names["ticker_name"], strict=True)) if not names.empty else {}

    items = [
        {
            "ticker": code,
            "name": name_map.get(code, code),
            "profit": float(p),
            "realized": float(realized.get(code, 0.0)),
        }
        for code, p in profit.items()
    ]
    items.append(
        {
            "ticker": "CASH",
            "name": "현금·예수금 (환율·이자 등)",
            "profit": total_profit - float(profit.sum()),
            "realized": 0.0,
        }
    )
    items.sort(key=lambda it: it["profit"], reverse=True)

    return {
        "start": twr.index[1].strftime("%Y-%m-%d"),
        "end": end_day,
        "total_profit": total_profit,
        "base_eval": float(twr["eval_amount"].iloc[0]),
        "items": items,
    }
