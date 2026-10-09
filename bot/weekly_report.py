"""bot/weekly_report.py - 주간 성과 리포트 (일요일 08:00 자동 발송, /weekly 수동 조회)

기준 주간: 종료일(가장 최근 결산 스냅샷, 기본 어제까지) 7일 전 스냅샷 → 종료일.
    일요일 아침 발송 시 토요일 16:00 스냅샷(금요일 미국장 종가 반영)까지를 한 주로 본다.
구성: 요약 텍스트(HTML) + 차트 2장 (주간 종목 기여도, 최근 3개월 수익률 비교)
"""

import html
import logging
from datetime import datetime, timedelta

import pandas as pd
from telegram import InputMediaPhoto, Update
from telegram.ext import ContextTypes

from core.formatter import _format_eok_man
from core.performance import (
    _to_krw,
    get_drawdown_report,
    get_fx_attribution,
    get_monthly_pnl,
    get_ticker_contribution,
    get_twr_series,
)
from database.connection import read_df

BENCHMARKS = [("KS11", "KOSPI"), ("US500", "S&amp;P500"), ("KRW-BTC", "BTC")]


def _latest_snapshot_date(on_or_before: str) -> str | None:
    df = read_df(
        "SELECT MAX(snapshot_date) AS d FROM daily_snapshots WHERE snapshot_date <= ?", (on_or_before,)
    )
    if df.empty or pd.isna(df["d"].iloc[0]):
        return None
    return str(df["d"].iloc[0])[:10]


def _benchmark_return(ticker: str, base_date: str, end_date: str) -> float | None:
    """기준일·종료일 이전 최신 종가 기준 수익률 (%)."""
    df = read_df(
        """
        SELECT
            (SELECT close_price FROM daily_prices WHERE ticker_code = ? AND price_date <= ? ORDER BY price_date DESC LIMIT 1) AS p0,
            (SELECT close_price FROM daily_prices WHERE ticker_code = ? AND price_date <= ? ORDER BY price_date DESC LIMIT 1) AS p1
        """,
        (ticker, base_date, ticker, end_date),
    )
    if df.empty or df.isna().any(axis=None):
        return None
    p0, p1 = float(df["p0"].iloc[0]), float(df["p1"].iloc[0])
    return (p1 / p0 - 1) * 100 if p0 else None


def _signed_eok_man(amount: float) -> str:
    return ("+" if amount > 0 else "") + _format_eok_man(amount)


def build_weekly_report(end_date: str | None = None) -> dict | None:
    """
    주간 리포트 데이터·텍스트 생성.
    Returns: {"text", "start", "end", "contribution"} / 스냅샷 부족 시 None
    """
    end_limit = end_date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    end = _latest_snapshot_date(end_limit)
    if not end:
        return None
    end_ts = pd.Timestamp(end)
    week_start = (end_ts - pd.Timedelta(days=6)).strftime("%Y-%m-%d")
    base = (end_ts - pd.Timedelta(days=7)).strftime("%Y-%m-%d")

    week = get_twr_series(base, end)
    if len(week) < 2:
        return None

    eval_end = float(week["eval_amount"].iloc[-1])
    flows = float(week["net_flow"].iloc[1:].sum())
    profit = eval_end - float(week["eval_amount"].iloc[0]) - flows
    week_return = (week["index"].iloc[-1] / week["index"].iloc[0] - 1) * 100

    start_label = week.index[1].strftime("%m-%d")
    end_label = end_ts.strftime("%m-%d")
    lines = [f"🗓 <b>주간 성과 리포트</b> ({start_label} ~ {end_label})", ""]

    flow_note = f", 순입금 {_signed_eok_man(flows)} 제외" if abs(flows) >= 1 else ""
    lines.append(f"💰 총자산 <b>{_format_eok_man(eval_end)}</b> (주간 {_signed_eok_man(profit)}{flow_note})")
    lines.append(f"📈 주간 수익률 <b>{week_return:+.2f}%</b>")
    bm = []
    for ticker, name in BENCHMARKS:
        r = _benchmark_return(ticker, base, end)
        if r is not None:
            bm.append(f"{name} {r:+.2f}%")
    if bm:
        lines.append("    " + " · ".join(bm))

    # 이번 달·성과 측정 시작 이후 누적
    month = get_monthly_pnl(end_ts.replace(day=1).strftime("%Y-%m-%d"), end)
    total = get_twr_series(None, end)
    if month["months"]:
        lines.append(
            f"📅 {end_ts.month}월 누적 {month['returns'][-1]:+.2f}% ({_signed_eok_man(month['profit'][-1])})"
            f" · 측정 시작 후 {total['cum_return'].iloc[-1]:+.2f}%"
        )

    # 낙폭: 현재 고점 대비 + 최대 낙폭
    dd = get_drawdown_report(None, end)
    if dd["dates"]:
        current_dd = dd["portfolio"]["drawdown"][-1]
        peak_date = total["index"].idxmax().strftime("%m-%d")
        mdd = dd["portfolio"]["metrics"]["mdd"]
        if current_dd < 0:
            lines.append(f"📉 고점 대비 {current_dd:.1f}% (고점 {peak_date}) · 최대 낙폭 {mdd:.1f}%")
        else:
            lines.append(f"🏔 고점 경신 중 · 최대 낙폭 {mdd:.1f}%")

    # 주간 종목 기여 상위·하위
    contribution = get_ticker_contribution(week_start, end)
    items = [it for it in contribution["items"] if it["ticker"] != "CASH" and it["profit"] != 0]
    top = [it for it in items if it["profit"] > 0][:3]
    bottom = [it for it in items if it["profit"] < 0][::-1][:3]
    if top or bottom:
        lines.append("")
    if top:
        lines.append(
            "🟢 " + " · ".join(f"{html.escape(it['name'])} {_signed_eok_man(it['profit'])}" for it in top)
        )
    if bottom:
        lines.append(
            "🔴 " + " · ".join(f"{html.escape(it['name'])} {_signed_eok_man(it['profit'])}" for it in bottom)
        )

    # 환율
    fx = get_fx_attribution(week_start, end)
    if fx["items"]:
        lines.append("")
        lines.append(
            f"💱 USD/KRW {fx['fx_start']:,.1f} → {fx['fx_end']:,.1f} ({fx['fx_change']:+.2f}%)"
            f" · 달러 자산 환율 효과 {_signed_eok_man(fx['fx_effect'])}"
        )

    # 주간 배당 입금
    divs = read_df(
        """
        SELECT trans_date, ticker_name, total_amount, currency FROM transactions
        WHERE action_type = 'DIVIDEND' AND trans_date BETWEEN ? AND ?
        """,
        (week_start, end),
    )
    if not divs.empty:
        divs["trans_date"] = pd.to_datetime(divs["trans_date"])
        divs["krw"] = _to_krw(divs, "trans_date", "total_amount", end)
        names = " · ".join(html.escape(n) for n in divs["ticker_name"].unique()[:3])
        lines.append(f"💵 배당 입금 {len(divs)}건 {divs['krw'].sum():+,.0f}원 ({names})")

    # 목표 비중 이탈 (목표 설정 시)
    from core.rebalance import format_drift_line, get_rebalance_status

    rebalance = get_rebalance_status()
    targeted = [row for row in rebalance["rows"] if row["target_pct"] is not None]
    if targeted:
        drifts = [row for row in targeted if row["out_of_band"]]
        lines.append("")
        if drifts:
            lines.append(f"⚖️ 목표 비중 이탈 (허용 ±{rebalance['band_pct']:.1f}%p)")
            lines += [f"    {html.escape(format_drift_line(row))}" for row in drifts]
        else:
            lines.append(f"⚖️ 목표 비중 모두 허용 범위 내 (±{rebalance['band_pct']:.1f}%p)")

    return {"text": "\n".join(lines), "start": week_start, "end": end, "contribution": contribution}


async def send_weekly_report(bot, chat_id, end_date: str | None = None) -> bool:
    """주간 리포트 텍스트 + 차트 2장 발송. 데이터 부족 시 False."""
    from bot.chart_renderer import render_comparison_chart, render_contribution_chart
    from core.calculator import get_performance_comparison

    report = build_weekly_report(end_date)
    if report is None:
        logging.warning("⚠️ 주간 리포트: 스냅샷 데이터 부족으로 발송 생략")
        return False

    await bot.send_message(chat_id=chat_id, text=report["text"], parse_mode="HTML")

    photos = []  # (이미지 버퍼, 캡션)
    try:
        if report["contribution"]["items"]:
            photos.append((render_contribution_chart(report["contribution"]), "🧩 주간 종목 기여도"))
        start_3m = (pd.Timestamp(report["end"]) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
        comparison = get_performance_comparison(start_3m, report["end"], benchmark_tickers=["KS11", "US500"])
        if comparison["dates"]:
            photos.append((render_comparison_chart(comparison), "📈 최근 3개월 수익률 비교 (TWR)"))
        if len(photos) > 1:
            await bot.send_media_group(
                chat_id=chat_id, media=[InputMediaPhoto(buf, caption=caption) for buf, caption in photos]
            )
        elif photos:
            await bot.send_photo(chat_id=chat_id, photo=photos[0][0], caption=photos[0][1])
    except Exception as e:
        logging.error(f"❌ 주간 리포트 차트 생성 실패: {e}", exc_info=True)
    return True


async def weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/weekly: 주간 성과 리포트 즉시 조회 (관리자 전용)."""
    from bot.handlers.report_handler import _check_admin

    if not await _check_admin(update):
        return
    sent = await send_weekly_report(context.bot, update.effective_chat.id)
    if not sent:
        await update.effective_message.reply_text("📉 주간 리포트를 만들 스냅샷 데이터가 없습니다.")
    logging.info("✅ /weekly 명령어 응답 완료")
