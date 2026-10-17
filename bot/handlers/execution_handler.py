"""bot/handlers/execution_handler.py - 증권사 체결 대기 알림과 승인 버튼 (callback_data 접두어 'ex:')

- 수집기가 broker_executions 에 쌓은 PENDING 체결을 정해진 시각에 알리고, ✅ 기록 버튼으로 원장에 기록
- 버튼은 id 만 담은 무상태 콜백이라 봇 재시작·오래된 메시지에서도 동작 (이미 처리된 건은 안내만)
- 메뉴 '📥 체결 대기'(menu:executions): 알림을 놓쳤을 때 현재 대기 건을 다시 보여줌
"""

import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.conversation import fmt_money
from bot.handlers.report_handler import _check_admin, _safe_edit
from core import executions as ex

DIGEST_THRESHOLD = 5  # 이 건수를 넘으면 건별 메시지 대신 목록 한 개 (도입 직후 대량 유입 보호)
SIDE_LABELS = {"BUY": "매수", "SELL": "매도"}


def _fmt_qty(value: float) -> str:
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def _describe(e: dict, name: str) -> str:
    total = ex.calc_total(e["quantity"], e["unit_price"], e["currency"])
    return (
        f"<b>{html.escape(e['account_name'])}</b> · {SIDE_LABELS[e['side']]}\n"
        f"{html.escape(name)} ({html.escape(e['ticker_code'])}) {_fmt_qty(e['quantity'])}주 "
        f"@ {fmt_money(e['unit_price'], e['currency'])}\n"
        f"총액 {fmt_money(total, e['currency'])} · {e['trade_date']}"
    )


def _buttons(e: dict) -> InlineKeyboardMarkup:
    first = (
        InlineKeyboardButton("✅ 그래도 기록", callback_data=f"ex:ok:{e['id']}")
        if e["status"] == "DUPLICATE"
        else InlineKeyboardButton("✅ 기록", callback_data=f"ex:ok:{e['id']}")
    )
    return InlineKeyboardMarkup([[first, InlineKeyboardButton("✖ 무시", callback_data=f"ex:no:{e['id']}")]])


async def _send_item(bot, chat_id, e: dict, title: str) -> None:
    name = await asyncio.to_thread(ex.ticker_name, e["ticker_code"])
    await bot.send_message(
        chat_id=chat_id,
        text=f"{title}\n{_describe(e, name)}",
        parse_mode="HTML",
        reply_markup=_buttons(e),
    )
    await asyncio.to_thread(ex.mark_notified, e["id"])


async def _send_digest(bot, chat_id, items: list[dict]) -> None:
    lines = [f"📥 <b>새 체결 {len(items)}건</b>", ""]
    for e in items:
        lines.append(
            f"• {html.escape(e['account_name'])} {SIDE_LABELS[e['side']]} {html.escape(e['ticker_code'])} "
            f"{_fmt_qty(e['quantity'])}주 @ {fmt_money(e['unit_price'], e['currency'])} ({e['trade_date']})"
        )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 전부 기록", callback_data="ex:all"),
                InlineKeyboardButton("🔎 건별 보기", callback_data="ex:list"),
            ]
        ]
    )
    await bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML", reply_markup=markup)
    for e in items:
        await asyncio.to_thread(ex.mark_notified, e["id"])


async def notify_pending_executions(application, chat_id: str) -> None:
    """[평일 16:35 / 화~토 07:35] 새 대기 체결 알림. 이미 알린 건(notified_at)은 다시 보내지 않는다."""
    try:
        duplicates = await asyncio.to_thread(ex.classify_pending)
        pending = await asyncio.to_thread(ex.list_executions, "PENDING", True)
        if not pending and not duplicates:
            logging.info("📥 체결 대기 알림: 새 체결 없음")
            return
        bot = application.bot
        if len(pending) > DIGEST_THRESHOLD:
            await _send_digest(bot, chat_id, pending)
        else:
            for e in pending:
                await _send_item(bot, chat_id, e, "📥 <b>새 체결</b>")
        if duplicates:
            await bot.send_message(
                chat_id=chat_id,
                text=f"원장에 이미 기록된 것으로 보여 건너뛴 체결이 {len(duplicates)}건 있습니다. 아래에서 필요하면 기록하세요.",
            )
            for e in duplicates:
                await _send_item(bot, chat_id, e, "↪️ <b>이미 기록된 것으로 보임</b>")
        logging.info(f"📥 체결 대기 알림 발송: 대기 {len(pending)}건, 중복 {len(duplicates)}건")
    except Exception as e:
        logging.error(f"❌ 체결 대기 알림 실패: {e}", exc_info=True)


async def executions_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """메뉴 '📥 체결 대기': 현재 PENDING 전부 (알림 여부와 무관)."""
    if not await _check_admin(update):
        return
    await asyncio.to_thread(ex.classify_pending)
    pending = await asyncio.to_thread(ex.list_executions, "PENDING")
    message = update.effective_message
    if not pending:
        await message.reply_text("📥 대기 중인 체결이 없습니다.")
        return
    for e in pending:
        name = await asyncio.to_thread(ex.ticker_name, e["ticker_code"])
        await message.reply_text(
            f"📥 <b>대기 체결</b>\n{_describe(e, name)}", parse_mode="HTML", reply_markup=_buttons(e)
        )


async def _result_text(result: str, e: dict | None, done_label: str) -> str:
    if result == "missing" or e is None:
        return "⚠️ 해당 체결을 찾을 수 없습니다."
    name = await asyncio.to_thread(ex.ticker_name, e["ticker_code"])
    body = _describe(e, name)
    if result == "ok":
        return f"{done_label}\n{body}"
    if result == "already":
        status = {"APPROVED": "기록 완료", "REJECTED": "무시함"}.get(e["status"], e["status"])
        return f"ℹ️ 이미 처리된 체결입니다 ({status})\n{body}"
    return f"❌ 원장 기록 중 오류가 발생했습니다. 다시 눌러 주세요.\n{body}"


async def exec_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ex:ok:<id> / ex:no:<id> / ex:all / ex:list"""
    query = update.callback_query
    await query.answer()
    if not await _check_admin(update):
        return
    parts = query.data.split(":")
    action = parts[1]

    if action in ("ok", "no") and len(parts) == 3 and parts[2].isdigit():
        exec_id = int(parts[2])
        if action == "ok":
            result, e = await asyncio.to_thread(ex.approve, exec_id)
            text = await _result_text(result, e, "✅ <b>기록 완료</b>")
            # 실패하면 같은 버튼을 남겨 다시 시도할 수 있게 한다
            markup = _buttons(e) if result == "failed" and e else None
        else:
            result, e = await asyncio.to_thread(ex.reject, exec_id)
            text = await _result_text(result, e, "✖ <b>무시했습니다</b>")
            markup = None
        await _safe_edit(query, text, reply_markup=markup)
        return

    if action == "all":
        items = await asyncio.to_thread(ex.list_executions, "PENDING")
        results = [(await asyncio.to_thread(ex.approve, e["id"]))[0] for e in items]
        done, failed = results.count("ok"), results.count("failed")
        text = f"✅ <b>{done}건 기록 완료</b>" + (
            f"\n❌ {failed}건은 실패했습니다. '📥 체결 대기'에서 다시 확인하세요." if failed else ""
        )
        await _safe_edit(query, text)
        return

    if action == "list":
        await executions_command(update, context)
        return

    logging.warning(f"⚠️ 알 수 없는 체결 버튼: {query.data}")
