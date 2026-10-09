"""bot/handlers/alert_handler.py - /alert 이상징후 감시 기준값 조회·변경

사용법:
    /alert                     기준값 목록 + 버튼(➖/➕)으로 단계 조정
    /alert set 국내급락 4      값 지정 (별칭 또는 setting_key, 허용 범위 검증)
    /alert reset 국내급락|all  기본값 복원
    /alert log                 최근 7일 알림 발송 기록
변경값은 detector_settings 에 저장되어 다음 감시 주기부터 바로 반영된다.
"""

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.detector.anomaly import DEFAULT_SETTINGS
from core.rebalance import DEFAULT_BAND_PCT
from database.repository import AssetRepository

# (setting_key, 별칭, 라벨, 최소, 최대, 버튼 단위) — 버튼 콜백은 목록 순번을 사용
SETTINGS_META = [
    ("kr_prev_close_drop_pct", "국내급락", "🇰🇷 국내 전일 대비 하락", 0.5, 30.0, 0.5),
    ("kr_high_drawdown_pct", "국내고점", "🇰🇷 국내 당일 고점 대비 낙폭", 0.5, 30.0, 0.5),
    ("us_prev_close_drop_pct", "미국급락", "🇺🇸 미국 전일 대비 하락", 0.5, 30.0, 0.5),
    ("us_high_drawdown_pct", "미국고점", "🇺🇸 미국 당일 고점 대비 낙폭", 0.5, 30.0, 0.5),
    ("crypto_prev_close_drop_pct", "코인급락", "🪙 코인 전일(09시) 대비 하락", 0.5, 50.0, 0.5),
    ("crypto_high_drawdown_pct", "코인고점", "🪙 코인 당일 고점 대비 낙폭", 0.5, 50.0, 0.5),
    ("premarket_gap_pct", "장전갭", "🌅 장전 S&P500 등락 (±)", 0.5, 20.0, 0.5),
    ("escalation_step_pct", "재알림", "🔁 같은 날 재알림 추가 하락폭", 0.5, 20.0, 0.5),
    ("rebalance_band_pct", "리밸런싱", "⚖️ 목표 비중 허용 이탈폭 (%p)", 0.5, 50.0, 1.0),
]
DEFAULTS = {**DEFAULT_SETTINGS, "rebalance_band_pct": DEFAULT_BAND_PCT}
_BY_KEY = {meta[0]: meta for meta in SETTINGS_META}
_BY_ALIAS = {meta[1]: meta for meta in SETTINGS_META}

EVENT_LABELS = {
    "PREV_CLOSE_DROP": "전일 대비 하락",
    "HIGH_DRAWDOWN": "고점 대비 낙폭",
    "PREMARKET_GAP": "장전 갭",
    "REBALANCE_DRIFT": "목표 비중 이탈",
}

USAGE = (
    "<code>/alert set 국내급락 4</code> · <code>/alert reset 국내급락</code> · <code>/alert reset all</code>\n"
    "<code>/alert log</code> 최근 7일 알림 기록\n"
    "별칭: " + ", ".join(meta[1] for meta in SETTINGS_META)
)


def _resolve(name: str):
    return _BY_ALIAS.get(name) or _BY_KEY.get(name.lower())


def _current_values() -> dict[str, float]:
    values = dict(DEFAULTS)
    values.update(AssetRepository().get_detector_settings())
    return values


def build_settings_text(values: dict[str, float]) -> str:
    lines = ["🚨 <b>이상징후 감시 기준값</b> (단위 %, 다음 감시 주기부터 반영)", ""]
    for key, alias, label, *_ in SETTINGS_META:
        value = values.get(key, DEFAULTS[key])
        changed = "" if abs(value - DEFAULTS[key]) < 1e-9 else f" (기본 {DEFAULTS[key]:g})"
        lines.append(f"{label}: <b>{value:g}</b>{changed} · <i>{alias}</i>")
    lines.append("")
    lines.append(USAGE)
    return "\n".join(lines)


def settings_keyboard(values: dict[str, float]) -> InlineKeyboardMarkup:
    rows = []
    for i, (key, alias, *_rest) in enumerate(SETTINGS_META):
        rows.append(
            [
                InlineKeyboardButton("➖", callback_data=f"alert:{i}:-"),
                InlineKeyboardButton(
                    f"{alias} {values.get(key, DEFAULTS[key]):g}", callback_data="alert:noop"
                ),
                InlineKeyboardButton("➕", callback_data=f"alert:{i}:+"),
            ]
        )
    return InlineKeyboardMarkup(rows)


def _validate(meta, value: float) -> str | None:
    _, alias, _, low, high, _ = meta
    if not low <= value <= high:
        return f"{alias}: {low:g} ~ {high:g} 사이로 입력하세요."
    return None


def _save(meta, value: float) -> None:
    key, _, label, *_ = meta
    AssetRepository().set_detector_setting(key, round(value, 2), label)


def build_alert_log_text(days: int = 7) -> str:
    rows = AssetRepository().get_recent_anomaly_alerts(days)
    if not rows:
        return f"🔕 최근 {days}일 알림 기록이 없습니다."
    from core.performance import _read_sql

    names = _read_sql(
        "SELECT ticker_code, MAX(ticker_name) AS ticker_name FROM transactions WHERE ticker_code IS NOT NULL GROUP BY ticker_code",
        (),
    )
    name_map = dict(zip(names["ticker_code"], names["ticker_name"], strict=True)) if not names.empty else {}
    name_map.update({"US500": "S&P500", "KS11": "KOSPI"})

    lines = [f"🧾 <b>최근 {days}일 알림 기록</b> (최신순)", ""]
    for r in rows:
        event = EVENT_LABELS.get(r["event_type"], r["event_type"])
        target = str(r["ticker_code"])
        if target.startswith("REBAL:"):
            target = target.split(":")[1]
            value = f"{float(r['change_pct']):+.1f}%p"
        else:
            target = name_map.get(target, target)
            value = f"{float(r['change_pct']):+.2f}%"
        repeat = f" ×{r['alert_count']}" if r["alert_count"] and r["alert_count"] > 1 else ""
        lines.append(f"{str(r['alert_date'])[5:10]} {html.escape(target)} {event} {value}{repeat}")
    return "\n".join(lines)


async def alert_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.handlers.report_handler import _check_admin

    if not await _check_admin(update):
        return

    args = context.args or []
    sub = args[0].lower() if args else ""
    reply = update.effective_message.reply_text

    try:
        if sub == "log":
            await reply(build_alert_log_text(), parse_mode="HTML")
            return
        if sub == "set":
            meta = _resolve(args[1]) if len(args) > 1 else None
            try:
                value = float(args[2].rstrip("%")) if len(args) > 2 else None
            except ValueError:
                value = None
            if meta is None or value is None:
                await reply("항목과 값을 확인하세요.\n" + USAGE, parse_mode="HTML")
                return
            error = _validate(meta, value)
            if error:
                await reply(error)
                return
            _save(meta, value)
        elif sub == "reset":
            target = args[1] if len(args) > 1 else ""
            metas = SETTINGS_META if target.lower() == "all" else [_resolve(target)] if target else [None]
            if None in metas:
                await reply("초기화할 항목을 확인하세요.\n" + USAGE, parse_mode="HTML")
                return
            for meta in metas:
                _save(meta, DEFAULTS[meta[0]])

        values = _current_values()
        await reply(build_settings_text(values), parse_mode="HTML", reply_markup=settings_keyboard(values))
        logging.info(f"✅ /alert 명령어 응답 완료 ({sub or 'list'})")
    except Exception as e:
        logging.error(f"❌ /alert 처리 실패: {e}", exc_info=True)
        await reply("⚠️ 감시 기준값 처리 중 오류가 발생했습니다.")


async def alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """alert:<순번>:<+|-> → 버튼 단위만큼 조정 후 같은 메시지 갱신. alert:noop 은 무시."""
    from bot.handlers.report_handler import _check_admin, _safe_edit

    query = update.callback_query
    parts = query.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or int(parts[1]) >= len(SETTINGS_META):
        await query.answer()
        return
    if not await _check_admin(update):
        await query.answer()
        return

    meta = SETTINGS_META[int(parts[1])]
    key, alias, _, low, high, step = meta
    values = _current_values()
    new_value = round(values.get(key, DEFAULTS[key]) + (step if parts[2] == "+" else -step), 2)
    if not low <= new_value <= high:
        await query.answer(f"{alias}: {low:g} ~ {high:g} 범위를 벗어납니다.")
        return

    _save(meta, new_value)
    values[key] = new_value
    await query.answer(f"{alias} → {new_value:g}")
    await _safe_edit(query, build_settings_text(values), reply_markup=settings_keyboard(values))
    logging.info(f"⚙️ 감시 기준값 변경: {key} = {new_value}")
