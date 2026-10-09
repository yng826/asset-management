import html
import logging
import re
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config.settings import ADMIN_USER_ID
from core.calculator import (
    enrich_holdings_with_prices,
    get_account_cash_map,
    get_latest_fx_rate,
    get_latest_prices_map,
)
from core.formatter import build_status_chunks, build_status_summary
from core.live_tracker import get_live_tracker_status
from core.performance import has_estimated_period
from database.repository import AssetRepository

# 성과 차트 캡션: 조회 구간이 실제 거래 기록 시작일(core.performance.LEDGER_TRACKING_START) 이전을 포함할 때
ESTIMATED_NOTE = "<i>※ 09-01 이전은 9월 보유분 기준 추정치 (실제 거래 기록은 09-01부터)</i>"


def _get_safe_admin_id():
    """ADMIN_USER_ID에서 따옴표를 정규화하고 정수형으로 변환합니다."""
    return int(str(ADMIN_USER_ID).strip("'\"")) if ADMIN_USER_ID else None


async def _check_admin(update: Update) -> bool:
    """사용자가 관리자인지 확인합니다."""
    admin_id = _get_safe_admin_id()
    if update.effective_user.id != admin_id:
        logging.warning(
            f"🚫 비관리자 접근 시도: {update.effective_user.id} (요청 명령어: {update.effective_message.text if update.effective_message else ''})"
        )
        await update.effective_message.reply_text("🚫 관리자 전용 명령어입니다.")
        return False
    return True


def _parse_period_days(token: str) -> int | None:
    """기간 토큰을 일수로 변환 (예: '14' → 14, '2w' → 14, '1m' → 30, '1y' → 365). 실패 시 None."""
    match = re.match(r"^(\d+)([dwmy]?)$", token.strip().lower())
    if not match:
        return None
    amount = int(match.group(1))
    multiplier = {"": 1, "d": 1, "w": 7, "m": 30, "y": 365}[match.group(2)]
    days = amount * multiplier
    return days if days > 0 else None


async def _safe_edit(query, text: str, reply_markup=None, parse_mode: str | None = "HTML") -> None:
    """콜백 원본 메시지 수정 (내용이 동일해 'Message is not modified'가 나는 경우는 무시)."""
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


# /pnl 기간 선택 버튼 (라벨, 토큰)
PNL_PERIODS = [("1주", "1w"), ("2주", "2w"), ("1개월", "1m"), ("3개월", "3m")]


def _pnl_keyboard(selected: str | None = None) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"✅ {label}" if token == selected else label, callback_data=f"pnl:{token}")
        for label, token in PNL_PERIODS
    ]
    return InlineKeyboardMarkup([buttons])


def _build_pnl_message(days: int) -> str:
    """최근 N일 일별 손익 메시지 생성."""
    repo = AssetRepository()
    # 최근 N일치 데이터 조회 (repository는 DESC 정렬해서 주므로 그대로 활용)
    all_history = repo.get_daily_pnl_history()
    target_history = all_history[:days]

    if not target_history:
        return "📉 최근 조회 가능한 손익 데이터가 없습니다."

    from core.formatter import format_pnl_daily

    # 16:00 일일 결산 전에 생성된 당일 스냅샷은 잠정치로 표기
    today = datetime.now().strftime("%Y-%m-%d")
    provisional_date = None
    if target_history[0]["snapshot_date"] == today and not repo.has_batch_run("closing_1600", today):
        provisional_date = today

    message = format_pnl_daily(target_history, provisional_date)
    if len(message) > 4000:
        message = message[:3950] + "\n\n...(이하 생략)..."
    return message


async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pnl 명령어: 최근 N일간 일별 손익 추이 조회 (+ 기간 선택 버튼).

    - /pnl          : 최근 7일
    - /pnl 14, 2w, 1m 등 직접 입력도 지원
    """
    logging.info(f"⚡ /pnl 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    if not await _check_admin(update):
        return

    # 파라미터 처리
    token = context.args[0] if context.args else "1w"
    days = _parse_period_days(token)
    if days is None:
        await update.effective_message.reply_text(
            "⚠️ 올바른 기간을 입력하세요. 예: /pnl 14, /pnl 2w, /pnl 1m", reply_markup=_pnl_keyboard()
        )
        return

    try:
        message = _build_pnl_message(days)
        await update.effective_message.reply_text(
            message, parse_mode="HTML", reply_markup=_pnl_keyboard(token)
        )
        logging.info(f"✅ /pnl 명령어 응답 완료 (최근 {days}일)")

    except Exception as e:
        logging.error(f"❌ /pnl 명령어 실행 실패: {e}")
        await update.effective_message.reply_text("⚠️ 손익 리포트 생성 중 오류가 발생했습니다.")


async def pnl_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pnl 기간 버튼 콜백 (pnl:<token>): 같은 메시지를 선택 기간으로 갱신."""
    query = update.callback_query
    await query.answer()
    if not await _check_admin(update):
        return

    token = query.data.split(":", 1)[1]
    days = _parse_period_days(token)
    if days is None:
        return

    try:
        message = _build_pnl_message(days)
        await _safe_edit(query, message, reply_markup=_pnl_keyboard(token))
        logging.info(f"✅ /pnl 버튼 응답 완료 (최근 {days}일)")
    except Exception as e:
        logging.error(f"❌ /pnl 버튼 실행 실패: {e}")
        await update.effective_message.reply_text("⚠️ 손익 리포트 생성 중 오류가 발생했습니다.")


def _load_enriched_holdings() -> list:
    """DB → holdings → 가격 매핑 → enriched dict 리스트 적재 헬퍼.
    - /status, /details 핸들러 공통 사용.
    - holdings 집계 + daily_prices 조회 + USD/KRW 환율 매핑 + 평가 산출을 한 번에 수행.
    """
    logging.info("📊 보유 종목 및 가격 매핑 적재 시작...")
    repo = AssetRepository()
    logging.info("📊 현재 보유 종목 조회 중...")
    holdings = repo.get_current_holdings()
    logging.info(f"📊 현재 보유 종목 수: {len(holdings)}")
    price_map = get_latest_prices_map()
    logging.info(f"📊 최근 종가 매핑 수: {len(price_map)}")
    fx_rate = get_latest_fx_rate()
    logging.info(f"📊 USD/KRW 환율: {fx_rate}")
    return enrich_holdings_with_prices(holdings, price_map, fx_rate)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status 명령어: 포트폴리오 요약 조회."""
    logging.info(f"⚡ /status 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    if not await _check_admin(update):
        return

    try:
        enriched = _load_enriched_holdings()
        fx_rate = get_latest_fx_rate()
        cash_by_account = get_account_cash_map(fx_rate)
        message = build_status_summary(enriched, cash_by_account, fx_rate)
    except Exception as e:
        logging.error(f"❌ /status 요약 생성 실패: {e}")
        message = (
            "📊 <b>포트폴리오 한눈에 보기</b>\n\n"
            "리포트를 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n"
            "<i>(데이터는 실제와 다를 수 있습니다.)</i>"
        )

    if len(message) > 4000:
        message = message[:3950] + "\n\n...(이하 생략)..."

    await update.effective_message.reply_text(message, parse_mode="HTML")
    logging.info("✅ /status 명령어 응답 완료")


async def details_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/details 명령어: 계좌별 세부 보유 종목 조회."""
    logging.info(f"⚡ /details 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    if not await _check_admin(update):
        return

    try:
        enriched = _load_enriched_holdings()
        logging.info(f"📊 /details 상세 리포트 생성: {len(enriched)} 종목")
        # ⚠️ context 인자를 넘기지 않고 단독 호출하여 기본 chunk_size를 사용
        chunks = build_status_chunks(enriched)
    except Exception as e:
        logging.error(f"❌ /details 상세 리포트 생성 실패: {e}")
        chunks = [
            "📊 <b>보유 종목 상세</b>\n\n"
            "리포트를 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n"
            "<i>(데이터는 실제와 다를 수 있습니다.)</i>"
        ]

    for chunk in chunks:
        if len(chunk) > 4000:
            chunk = chunk[:3950] + "\n\n...(이하 생략)..."
        await update.effective_message.reply_text(chunk, parse_mode="HTML")

    logging.info("✅ /details 명령어 응답 완료")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.info(f"⚡ /history 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    repo = AssetRepository()
    transactions = repo.get_recent_transactions(limit=10)

    message = "📜 최근 거래 내역 (10건)\n\n"
    if not transactions:
        message += "최근 거래 내역이 없습니다.\n"
    else:
        for tx in transactions:
            trans_date = tx["trans_date"]
            account = tx["account_name"]
            ticker = tx["ticker_name"]
            action_type = tx["action_type"]
            quantity = tx["quantity"]
            unit_price = tx["unit_price"]
            total_amount = tx["total_amount"]
            memo = tx["memo"]

            if action_type == "BUY":
                action_emoji = "⬆️ 매수"
            elif action_type == "SELL":
                action_emoji = "⬇️ 매도"
            elif action_type == "DIVIDEND":
                action_emoji = "💰 배당"
            elif action_type == "DEPOSIT":
                action_emoji = "➕ 입금"
            elif action_type == "WITHDRAW":
                action_emoji = "➖ 출금"
            else:
                action_emoji = action_type

            message += (
                f"🗓️ {trans_date} [{account}]\n"
                f"  {action_emoji} {ticker} {quantity:,.2f}주 @ {unit_price:,.0f}원 = {total_amount:,.0f}원\n"
            )
            if memo:
                message += f"  (메모: {html.escape(memo)})\n"

    if len(message) > 4000:
        message = message[:3950] + "\n\n...(이하 생략)..."

    await update.effective_message.reply_text(message)
    logging.info("✅ /history 명령어 응답 완료")


def _period_start_date(period: str, default_days: int = 30) -> str:
    """기간 토큰(예: 2w, 3m, 1y) → 시작일 문자열. 형식이 아니면 default_days 전."""
    match = re.match(r"^(\d+)([dwmy])$", (period or "").lower())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit] * amount
    else:
        days = default_days
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def _yesterday() -> str:
    """성과 차트 종료일: 어제 (당일 결산 전 데이터 제외)."""
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


NO_SNAPSHOT_TEXT = "📉 해당 기간에 사용할 수 있는 스냅샷 데이터가 없습니다."


async def _send_chart(
    update: Update,
    name: str,
    load,
    is_empty,
    render,
    caption,
    error_text: str,
    empty_text: str = NO_SNAPSHOT_TEXT,
) -> None:
    """
    /chart 공통 응답: load() → is_empty(data) 면 empty_text 안내, 아니면 render(data) 이미지 + caption(data) (HTML) 전송.
    예외는 로그 후 error_text 안내. name 은 로그용 명령 이름 (예: '/chart dd').
    """
    try:
        data = load()
        if is_empty(data):
            await update.effective_message.reply_text(empty_text)
            logging.info(f"✅ {name} 명령어 응답 완료 (데이터 없음)")
            return
        await update.effective_message.reply_photo(
            photo=render(data), caption=caption(data), parse_mode="HTML"
        )
        logging.info(f"✅ {name} 명령어 응답 완료")
    except Exception as e:
        logging.error(f"❌ {name} 생성 실패: {e}", exc_info=True)
        await update.effective_message.reply_text(error_text)


async def _handle_comparison_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """자산 수익률 vs 지수 비교 차트 (기본 1개월)."""
    from bot.chart_renderer import render_comparison_chart
    from core.calculator import get_performance_comparison

    def caption(data: dict) -> str:
        text = "📈 수익률 비교 차트 (TWR)"
        if has_estimated_period(data["dates"][0]):
            text += "\n" + ESTIMATED_NOTE
        return text

    start_date = _period_start_date(period_args[0] if period_args else "", default_days=30)
    await _send_chart(
        update,
        "/chart",
        load=lambda: get_performance_comparison(
            start_date, _yesterday(), benchmark_tickers=["KS11", "KQ11", "US500", "KRW-BTC"]
        ),
        is_empty=lambda data: not data["dates"],
        render=render_comparison_chart,
        caption=caption,
        error_text="⚠️ 차트 생성 중 오류가 발생했습니다.",
    )


async def _handle_allocation_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """자산 배분 비중 차트 (기본 1개월)."""
    from bot.chart_renderer import render_allocation_chart
    from core.calculator import get_asset_allocation_history

    start_date = _period_start_date(period_args[0] if period_args else "", default_days=30)
    await _send_chart(
        update,
        "/chart alloc",
        load=lambda: get_asset_allocation_history(start_date, _yesterday()),
        is_empty=lambda data: not data["dates"] or not data["categories"],
        render=render_allocation_chart,
        caption=lambda data: "📈 자산 배분 누적 면적 차트 (/chart alloc)",
        error_text="⚠️ 자산 배분 차트 생성 중 오류가 발생했습니다.",
        empty_text="📉 해당 기간에 사용할 수 있는 자산 배분 스냅샷 데이터가 없습니다.",
    )


def _format_risk_caption(data: dict) -> str:
    """낙폭 차트 캡션 (HTML): 포트폴리오·벤치마크 리스크 지표 + MDD 구간."""
    from core.performance import RISK_FREE_RATE

    names = {"KS11": "KOSPI", "US500": "S&amp;P500"}

    def line(name: str, m: dict) -> str:
        sharpe = f"{m['sharpe']:.2f}" if m.get("sharpe") is not None else "-"
        return (
            f"<b>{name}</b>\n"
            f"  수익 {m['period_return']:+.1f}% · MDD {m['mdd']:.1f}% · 변동성 {m['volatility']:.0f}% · 샤프 {sharpe}"
        )

    p = data["portfolio"]["metrics"]
    lines = [f"📉 <b>낙폭·리스크</b> ({data['dates'][0][5:]} ~ {data['dates'][-1][5:]})", ""]
    lines.append(line("내 포트폴리오 (TWR)", p))
    if p["mdd"] < 0:
        recovery = (
            p["recovery_date"].strftime("%m-%d") + " 회복" if p["recovery_date"] is not None else "미회복"
        )
        lines.append(
            f"  MDD 구간 {p['peak_date'].strftime('%m-%d')} → {p['trough_date'].strftime('%m-%d')} ({recovery})"
        )
    for ticker, bm in data["benchmarks"].items():
        lines.append(line(names.get(ticker, html.escape(ticker)), bm["metrics"]))
    lines.append("")
    lines.append(f"<i>변동성·샤프 연환산, 무위험수익률 {RISK_FREE_RATE * 100:.1f}%</i>")
    if has_estimated_period(data["dates"][0]):
        lines.append(ESTIMATED_NOTE)
    return "\n".join(lines)


async def _handle_drawdown_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """낙폭 차트 + MDD·변동성·샤프 지표 캡션 (기본 3개월)."""
    from bot.chart_renderer import render_drawdown_chart
    from core.performance import get_drawdown_report

    start_date = _period_start_date(period_args[0] if period_args else "", default_days=90)
    await _send_chart(
        update,
        "/chart dd",
        load=lambda: get_drawdown_report(start_date, _yesterday()),
        is_empty=lambda data: not data["dates"],
        render=render_drawdown_chart,
        caption=_format_risk_caption,
        error_text="⚠️ 낙폭 차트 생성 중 오류가 발생했습니다.",
    )


def _format_monthly_caption(data: dict) -> str:
    """월별 손익 차트 캡션 (HTML): 월별 손익·수익률 목록 + 누적."""
    lines = ["📅 <b>월별 손익</b> (입출금 제외, 배당 포함)", ""]
    for month, profit, ret, partial in zip(
        data["months"], data["profit"], data["returns"], data["partial"], strict=True
    ):
        mark = "🟢" if profit >= 0 else "🔴"
        suffix = " (진행 중)" if partial else (" (추정)" if has_estimated_period(f"{month}-01") else "")
        lines.append(f"{mark} {month}  {profit:+,.0f}원 ({ret:+.1f}%){suffix}")
    lines.append("")
    lines.append(f"<b>누적 {data['cum_profit'][-1]:+,.0f}원</b>")
    if data["months"] and has_estimated_period(f"{data['months'][0]}-01"):
        lines.append(ESTIMATED_NOTE)
    return "\n".join(lines)


async def _handle_monthly_pnl_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """월별 손익 막대 차트 (기본 1년, 시작일은 해당 월 1일로 맞춤)."""
    from bot.chart_renderer import render_monthly_pnl_chart
    from core.performance import get_monthly_pnl

    start_date = _period_start_date(period_args[0] if period_args else "", default_days=365)
    await _send_chart(
        update,
        "/chart month",
        load=lambda: get_monthly_pnl(start_date, _yesterday()),
        is_empty=lambda data: not data["months"],
        render=render_monthly_pnl_chart,
        caption=_format_monthly_caption,
        error_text="⚠️ 월별 손익 차트 생성 중 오류가 발생했습니다.",
    )


def _format_contribution_caption(data: dict, top_n: int = 5) -> str:
    """종목 기여도 차트 캡션 (HTML): 기여 상위·하위 종목 + 실현(매도·배당) 표기."""
    items = [it for it in data["items"] if it["profit"] != 0]

    def line(it: dict) -> str:
        realized = f" · 실현 {it['realized']:,.0f}원" if it.get("realized") else ""
        return f"  {html.escape(it['name'])} {it['profit']:+,.0f}원{realized}"

    lines = [
        f"🧩 <b>종목별 수익 기여도</b> ({data['start'][5:]} ~ {data['end'][5:]})",
        f"기간 손익 <b>{data['total_profit']:+,.0f}원</b> (매도·배당 포함, 계좌 합산)",
        "",
        "🟢 <b>기여 상위</b>",
        *[line(it) for it in items if it["profit"] > 0][:top_n],
        "",
        "🔴 <b>기여 하위</b>",
        *[line(it) for it in items if it["profit"] < 0][::-1][:top_n],
    ]
    if has_estimated_period(data["start"]):
        lines += ["", ESTIMATED_NOTE]
    return "\n".join(lines)


async def _handle_contribution_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """종목별 수익 기여도 가로 막대 차트 (기본 1개월)."""
    from bot.chart_renderer import render_contribution_chart
    from core.performance import get_ticker_contribution

    start_date = _period_start_date(period_args[0] if period_args else "", default_days=30)
    await _send_chart(
        update,
        "/chart contrib",
        load=lambda: get_ticker_contribution(start_date, _yesterday()),
        is_empty=lambda data: not data["items"],
        render=render_contribution_chart,
        caption=_format_contribution_caption,
        error_text="⚠️ 종목 기여도 차트 생성 중 오류가 발생했습니다.",
    )


def _format_fx_caption(data: dict) -> str:
    """환율 효과 분리 캡션 (HTML): 환율 변동, 원화·달러 기준 수익률, 종목별 분해."""
    total = data["price_effect"] + data["fx_effect"]
    lines = [
        f"💱 <b>달러 자산 환율 효과</b> ({data['start'][5:]} ~ {data['end'][5:]})",
        f"USD/KRW {data['fx_start']:,.2f} → {data['fx_end']:,.2f} ({data['fx_change']:+.2f}%)",
        f"수익률: 달러 기준 {data['cum_usd'][-1]:+.2f}% / 원화 기준 {data['cum_krw'][-1]:+.2f}%",
        "",
        f"<b>손익 {total:+,.0f}원</b> = 주가 {data['price_effect']:+,.0f}원 + 환율 {data['fx_effect']:+,.0f}원",
        "",
    ]
    for it in data["items"]:
        lines.append(
            f"  {html.escape(it['name'])} {it['total']:+,.0f}원 (주가 {it['price_effect']:+,.0f} / 환율 {it['fx_effect']:+,.0f})"
        )
    lines.append("")
    lines.append("<i>미국 직접투자·달러 예수금 대상, 국내 상장 해외 ETF 제외. 주가 효과에 USD 배당 포함</i>")
    if has_estimated_period(data["start"]):
        lines.append(ESTIMATED_NOTE)
    return "\n".join(lines)


async def _handle_fx_attribution_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """달러 자산 손익의 주가 / 환율 효과 분리 차트 (기본 3개월)."""
    from bot.chart_renderer import render_fx_attribution_chart
    from core.performance import get_fx_attribution

    start_date = _period_start_date(period_args[0] if period_args else "", default_days=90)
    await _send_chart(
        update,
        "/chart fx",
        load=lambda: get_fx_attribution(start_date, _yesterday()),
        is_empty=lambda data: not data["items"],
        render=render_fx_attribution_chart,
        caption=_format_fx_caption,
        error_text="⚠️ 환율 효과 차트 생성 중 오류가 발생했습니다.",
        empty_text="📉 해당 기간에 달러 자산 스냅샷 데이터가 없습니다.",
    )


def _format_dividend_caption(data: dict) -> str:
    """배당 현황 캡션 (HTML): 연간 합계·전년 대비, 최근 12개월, 종목·계좌별."""
    year = data["year"]
    lines = [f"💵 <b>{year}년 배당 현황</b> ({data['count']}건)", ""]
    total_line = f"연간 누적 <b>{data['total']:,.0f}원</b>"
    if data["prev_monthly"] is not None and data["prev_total"] > 0:
        total_line += f" (전년 동기 {data['prev_total']:,.0f}원, {(data['total'] / data['prev_total'] - 1) * 100:+.0f}%)"
    lines.append(total_line)
    trailing = f"{data['trailing_label']} {data['trailing_12m']:,.0f}원"
    if data["yield_12m"] is not None:
        trailing += f" · 연환산 평가액 대비 {data['yield_12m']:.2f}%"
    lines.append(trailing)

    if data["by_ticker"]:
        lines.append("")
        lines.append("<b>종목별</b>")
        lines += [f"  {html.escape(name)} {amount:,.0f}원" for name, amount in data["by_ticker"][:8]]
    if data["by_account"]:
        lines.append("<b>계좌별</b>")
        lines += [f"  {html.escape(name)} {amount:,.0f}원" for name, amount in data["by_account"]]
    if data["first_date"]:
        lines.append("")
        lines.append(f"<i>원장 기록 기준 (첫 배당 기록 {data['first_date']}), USD는 입금일 환율</i>")
    return "\n".join(lines)


async def _handle_dividend_chart(update: Update, context: ContextTypes.DEFAULT_TYPE, year_args: list) -> None:
    """연간 월별 배당 차트 (/chart div [연도], 기본 올해)."""
    from bot.chart_renderer import render_dividend_chart
    from core.performance import get_dividend_summary

    year = int(year_args[0]) if year_args and str(year_args[0]).isdigit() else datetime.now().year
    await _send_chart(
        update,
        "/chart div",
        load=lambda: get_dividend_summary(year),
        is_empty=lambda data: data["count"] == 0,
        render=render_dividend_chart,
        caption=_format_dividend_caption,
        error_text="⚠️ 배당 차트 생성 중 오류가 발생했습니다.",
        empty_text=f"💵 {year}년 배당 기록이 없습니다.",
    )


# /chart 버튼 선택지: 차트 종류 (라벨, 키) / 기간 (라벨, 토큰)
CHART_TYPES = [
    ("📈 수익률 비교", "cmp"),
    ("📉 낙폭·리스크", "dd"),
    ("📅 월별 손익", "month"),
    ("🧩 종목 기여도", "contrib"),
    ("💱 환율 효과", "fx"),
    ("💵 배당", "div"),
    ("🥧 자산 배분", "alloc"),
    ("📊 금액 스택", "stack"),
]
CHART_PERIODS = [("1개월", "1m"), ("3개월", "3m"), ("6개월", "6m"), ("1년", "1y")]


def _chart_type_keyboard() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(label, callback_data=f"chart:{key}") for label, key in CHART_TYPES]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


def _chart_period_keyboard(chart_type: str) -> InlineKeyboardMarkup:
    periods = [
        InlineKeyboardButton(label, callback_data=f"chart:{chart_type}:{token}")
        for label, token in CHART_PERIODS
    ]
    back = InlineKeyboardButton("⬅️ 차트 종류", callback_data="chart:back")
    return InlineKeyboardMarkup([periods, [back]])


async def chart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chart 버튼 콜백.

    - chart:back            → 차트 종류 선택으로 복귀
    - chart:<type>          → 기간 선택 버튼 표시
    - chart:<type>:<period> → 차트 생성 후 사진으로 전송 (선택 메시지는 기간 버튼 유지)
    """
    query = update.callback_query
    parts = query.data.split(":")
    chart_labels = {key: label for label, key in CHART_TYPES}

    if len(parts) == 2:
        await query.answer()
        if parts[1] == "back":
            await _safe_edit(query, "📊 <b>어떤 차트를 볼까요?</b>", reply_markup=_chart_type_keyboard())
        elif parts[1] == "div":
            if await _check_admin(update):
                await _handle_dividend_chart(update, context, [])
        elif parts[1] in chart_labels:
            await _safe_edit(
                query,
                f"{chart_labels[parts[1]]} — <b>기간을 선택하세요</b>",
                reply_markup=_chart_period_keyboard(parts[1]),
            )
        return

    chart_type, period = parts[1], parts[2]
    if chart_type not in chart_labels:
        await query.answer()
        return

    await query.answer("⏳ 차트 생성 중...")
    if not await _check_admin(update):
        return

    if chart_type == "cmp":
        await _handle_comparison_chart(update, context, [period])
    elif chart_type == "dd":
        await _handle_drawdown_chart(update, context, [period])
    elif chart_type == "month":
        await _handle_monthly_pnl_chart(update, context, [period])
    elif chart_type == "contrib":
        await _handle_contribution_chart(update, context, [period])
    elif chart_type == "fx":
        await _handle_fx_attribution_chart(update, context, [period])
    elif chart_type == "alloc":
        await _handle_allocation_chart(update, context, [period])
    else:
        await _handle_stack_bar_chart(update, context, [period])


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /chart 명령어:
    - /chart alloc: 자산군별 비중(%) 정규화 area chart
    - /chart stack: 자산군별 절대금액 스택 바 차트
    - /chart dd [기간]: 낙폭 차트 + MDD·변동성·샤프 지표 (기본 3개월)
    - /chart month [기간]: 월별 손익 막대 차트 (기본 1년)
    - /chart contrib [기간]: 종목별 수익 기여도 차트 (매도·배당 포함, 기본 1개월)
    - /chart fx [기간]: 달러 자산 주가 / 환율 효과 분리 차트 (기본 3개월)
    - /chart div [연도]: 월별 배당 차트 (기본 올해, 버튼은 기간 선택 없이 바로 생성)
    """
    if not await _check_admin(update):
        return

    command = context.args[0] if context.args else None

    if command is None:
        # 인자 없이 호출 시 차트 종류 → 기간 순으로 버튼 선택
        await update.effective_message.reply_text(
            "📊 <b>어떤 차트를 볼까요?</b>", parse_mode="HTML", reply_markup=_chart_type_keyboard()
        )
        return

    if command == "alloc":
        from bot.chart_renderer import render_allocation_chart
        from core.calculator import get_asset_allocation_history

        data = get_asset_allocation_history()
        buf = render_allocation_chart(data)
        if not buf or (hasattr(buf, "getbuffer") and buf.getbuffer().nbytes == 0):
            await update.effective_message.reply_text("⚠️ 차트 이미지 데이터가 비어 있어 전송할 수 없습니다.")
            return
        buf.seek(0)
        await update.effective_message.reply_photo(photo=buf)

    elif command in ["dd", "risk"]:
        await _handle_drawdown_chart(update, context, context.args[1:])

    elif command in ["month", "monthly"]:
        await _handle_monthly_pnl_chart(update, context, context.args[1:])

    elif command in ["contrib", "contribution"]:
        await _handle_contribution_chart(update, context, context.args[1:])

    elif command == "fx":
        await _handle_fx_attribution_chart(update, context, context.args[1:])

    elif command in ["div", "dividend"]:
        await _handle_dividend_chart(update, context, context.args[1:])

    elif command in ["stack", "bar"]:
        await _handle_stack_bar_chart(update, context, context.args[1:])

    else:
        # 기존 로직 (비교 차트)
        await _handle_comparison_chart(update, context, context.args or [])


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/log 명령어: 관리자 전용 최신 로그 출력."""
    import os
    from collections import deque

    logging.info(f"⚡ /log 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    if not await _check_admin(update):
        return

    log_path = "logs/app.log"
    if not os.path.exists(log_path):
        await update.effective_message.reply_text("📂 로그 파일을 찾을 수 없습니다.")
        logging.info("✅ /log 명령어 응답 완료 (로그 파일 없음)")
        return

    try:
        # 파일 내용을 한꺼번에 읽지 않고 마지막 30줄만 안전하게 읽음
        with open(log_path, encoding="utf-8") as f:
            last_lines = deque(f, maxlen=30)

            message = "📜 최신 로그 (마지막 30줄):\n\n```\n" + "".join(last_lines) + "\n```"

            # 텔레그램 메시지 길이 제한 처리
            if len(message) > 4000:
                message = message[:3950] + "\n...(이하 생략)...```"

            await update.effective_message.reply_text(message, parse_mode="Markdown")
            logging.info("✅ /log 명령어 응답 완료")

    except Exception as e:
        logging.error(f"❌ /log 명령어 실행 실패: {e}")
        await update.effective_message.reply_text(f"⚠️ 로그 읽기 실패: {e}")


# /live 자산군 선택 버튼 (라벨, 인자, 제목)
LIVE_TYPES = [
    ("전체", "all", "전체 포트폴리오"),
    ("국내", "kr", "국내 주식"),
    ("미국", "us", "미국 주식"),
    ("코인", "crypto", "가상자산"),
]


def _live_keyboard(selected: str | None = None) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"✅ {label}" if key == selected else label, callback_data=f"live:{key}")
        for label, key, _ in LIVE_TYPES
    ]
    refresh = InlineKeyboardButton("🔄 새로고침", callback_data=f"live:{selected or 'all'}")
    return InlineKeyboardMarkup([buttons, [refresh]])


def _build_live_message(asset_type: str, title: str) -> str:
    """자산군별 실시간 현황 메시지 생성."""
    data = get_live_tracker_status(asset_type=asset_type)
    if not data or not data["assets"]:
        return f"📉 보유 중인 {title} 자산이 없습니다."

    assets_msg = ""
    for asset in data["assets"]:
        ticker_name = html.escape(asset.get("ticker_name") or asset["ticker"])
        diff_amt = asset["diff_amount"]
        if diff_amt > 0:
            icon = "🔺"
        elif diff_amt < 0:
            icon = "🔹"
        else:
            icon = "➖"

        # 미국 주식일 경우 달러 표기
        if asset.get("is_us"):
            price_str = f"${asset['price']:,.2f}"
            diff_str = f"{icon} ${abs(asset['diff_amount']):,.2f}"
        else:
            price_str = f"{asset['price']:,.0f}원"
            diff_str = f"{icon} {asset['diff_amount']:+,.0f}원"

        assets_msg += f"• <b>{ticker_name}</b>: {price_str} ({diff_str}, {asset['diff_rate']:+.2f}%)\n"

    total_diff = data["total_diff"]
    if total_diff > 0:
        icon = "🔺"
    elif total_diff < 0:
        icon = "🔹"
    else:
        icon = "➖"
    total_eval_str = f"{data['total_eval']:,.0f}원"
    if asset_type == "us" and data.get("fx_rate"):
        total_eval_str = f"${data['total_eval'] / data['fx_rate']:,.2f} (약 {data['total_eval']:,.0f}원)"

    message = (
        f"⚡ <b>{title} 실시간 현황 (Live)</b>\n"
        f"기준: {data['timestamp']}\n"
        f"---------------------------------\n"
        f"{assets_msg}"
        f"---------------------------------\n"
        f"총 평가액: <b>{total_eval_str}</b>\n"
        f"실시간 변동: {icon} <b>{data['total_diff']:+,.0f}원 ({data['total_diff_rate']:+.2f}%)</b>\n"
        f"<i>(전일 종가 대비 실시간 추산)</i>"
    )
    if len(message) > 4000:
        message = message[:3950] + "\n\n...(이하 생략)..."
    return message


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/live 명령어: 멀티 자산 실시간 현황 (+ 자산군 선택/새로고침 버튼)."""
    logging.info(
        f"⚡ /live 명령어 수신: {update.effective_user.id if update.effective_user else 'None'} "
        f"(args: {context.args})"
    )

    # 인자 분기 처리
    arg = context.args[0].lower() if context.args else "all"
    asset_type = "all"
    title = "전체 포트폴리오"

    if arg in ["kr", "stock"]:
        asset_type = "kr"
        title = "국내 주식"
    elif arg == "us":
        asset_type = "us"
        title = "미국 주식"
    elif arg in ["crypto", "coin"]:
        asset_type = "crypto"
        title = "가상자산"
    elif arg != "all":
        await update.effective_message.reply_text(
            "⚠️ 지원하지 않는 자산군입니다. (all, kr, us, crypto)", reply_markup=_live_keyboard()
        )
        return

    try:
        message = _build_live_message(asset_type, title)
        await update.effective_message.reply_text(
            message, parse_mode="HTML", reply_markup=_live_keyboard(asset_type)
        )
        logging.info(f"✅ /live {asset_type} 명령어 응답 완료")

    except Exception as e:
        logging.error(f"❌ /live 명령어 실행 실패: {e}")
        await update.effective_message.reply_text("⚠️ 실시간 현황 조회 중 오류가 발생했습니다.")


async def live_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/live 자산군·새로고침 버튼 콜백 (live:<type>): 같은 메시지를 갱신."""
    query = update.callback_query
    await query.answer("⏳ 조회 중...")

    asset_type = query.data.split(":", 1)[1]
    title = next((t for _, key, t in LIVE_TYPES if key == asset_type), None)
    if title is None:
        return

    try:
        message = _build_live_message(asset_type, title)
        await _safe_edit(query, message, reply_markup=_live_keyboard(asset_type))
        logging.info(f"✅ /live {asset_type} 버튼 응답 완료")
    except Exception as e:
        logging.error(f"❌ /live 버튼 실행 실패: {e}")
        await update.effective_message.reply_text("⚠️ 실시간 현황 조회 중 오류가 발생했습니다.")


async def send_status_report(bot, chat_id: str | int, title: str = "", full_report: bool = True) -> None:
    """스케줄러/배치 전용 리포트 발송 래퍼 함수"""
    try:
        from core.formatter import build_status_chunks, build_status_summary

        enriched = _load_enriched_holdings()

        if full_report:
            # 일일 결산 등 전체 상세 리포트 (청크 분할 발송)
            chunks = build_status_chunks(enriched)
            if title:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"📢 <b>[{title}]</b>",
                    parse_mode="HTML",
                )
            for chunk in chunks:
                if len(chunk) > 4000:
                    chunk = chunk[:3950] + "\n\n...(이하 생략)..."
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
        else:
            # 오전 브리핑 등 요약 리포트
            summary = build_status_summary(enriched)
            header = f"📢 <b>[{title}]</b>\n\n" if title else ""
            msg = header + summary
            if len(msg) > 4000:
                msg = msg[:3950] + "\n\n...(이하 생략)..."
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

        logging.info(f"✅ 정기 리포트 발송 완료: {title}")
    except Exception as e:
        logging.error(f"❌ 정기 리포트 발송 중 오류: {e}", exc_info=True)


async def _handle_stack_bar_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """자산군별 절대금액 스택 차트 (기본 1개월)."""
    from bot.chart_renderer import render_stack_bar_chart
    from core.calculator import get_asset_stack_eval_history

    start_date = _period_start_date(period_args[0] if period_args else "", default_days=30)
    await _send_chart(
        update,
        "/chart stack",
        load=lambda: get_asset_stack_eval_history(start_date, _yesterday()),
        is_empty=lambda data: not data["dates"] or not data["values"],
        render=render_stack_bar_chart,
        caption=lambda data: "📈 자산군별 절대금액 스택 바 차트 (/chart stack)",
        error_text="⚠️ 차트 생성 중 오류가 발생했습니다.",
    )
