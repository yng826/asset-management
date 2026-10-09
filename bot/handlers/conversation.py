"""bot/handlers/conversation.py - 버튼 단계 대화(거래 입력 /trade, 잔액 대사 /recon) 공통 함수

- 각 대화는 callback_data 접두어(예: 'tr', 'rc')와 context.user_data 키(예: 'trade', 'recon')로 구분
- 입력 숫자 파싱, 금액 표기, 메시지 렌더링, 취소·지난 버튼·저장 응답, ConversationHandler 조립
"""

import asyncio
import logging
import warnings

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters

from bot.handlers.report_handler import _check_admin, _safe_edit
from database.repository import AssetRepository

TEXT_INPUT = filters.TEXT & ~filters.COMMAND
CONVERSATION_TIMEOUT = 15 * 60


def parse_man_number(text: str) -> float | None:
    """'1234.5', '2.5만' → float. 숫자가 아니면 None (부호 검사는 호출부에서)."""
    multiplier = 1
    if text.endswith("만"):
        text, multiplier = text[:-1], 10000
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def fmt_money(value: float, currency: str) -> str:
    """'$1,234.50' (USD) / '1,235원' (KRW)."""
    return f"${value:,.2f}" if currency == "USD" else f"{value:,.0f}원"


def cancel_row(prefix: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("❌ 취소", callback_data=f"{prefix}:cancel")]


def account_rows(accounts: list[str], prefix: str) -> list[list[InlineKeyboardButton]]:
    """계좌 선택 버튼 (2열, callback_data '<prefix>:acc:<index>')."""
    return [
        [
            InlineKeyboardButton(name, callback_data=f"{prefix}:acc:{i}")
            for i, name in enumerate(accounts[j : j + 2], j)
        ]
        for j in range(0, len(accounts), 2)
    ]


async def render(update: Update, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """버튼에서 온 경우 같은 메시지를 수정, 텍스트 입력에서 온 경우 새 메시지로 응답."""
    if update.callback_query:
        await _safe_edit(update.callback_query, text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=markup)


async def begin(update: Update) -> bool:
    """대화 시작: 버튼이면 응답 처리 후 관리자 확인."""
    if update.callback_query:
        await update.callback_query.answer()
    return await _check_admin(update)


async def render_start(update: Update, text: str, markup: InlineKeyboardMarkup, new_data: str) -> None:
    """시작 화면: 메뉴 버튼(callback_data == new_data)에서 시작하면 메뉴 메시지는 남기고 새 메시지로 진행."""
    if update.callback_query and update.callback_query.data == new_data:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await render(update, text, markup)


async def save_transaction(query, data: dict, memo: str, label: str, success_text: str) -> None:
    """원장 저장 후 버튼 메시지를 결과로 수정. label 은 로그용 (예: '버튼 거래 입력')."""
    success = await asyncio.to_thread(AssetRepository().add_transaction, data, memo)
    if success:
        logging.info(f"✅ {label} 저장: {data}")
        await _safe_edit(query, success_text)
    else:
        logging.error(f"❌ {label} 저장 실패: {data}")
        await _safe_edit(query, "❌ DB 저장 중 오류가 발생했습니다. 다시 시도해 주세요.")


def make_cancel_handler(user_data_key: str, cancelled_text: str):
    async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        context.user_data.pop(user_data_key, None)
        if update.callback_query:
            await update.callback_query.answer()
            await _safe_edit(update.callback_query, cancelled_text)
        return ConversationHandler.END

    return on_cancel


async def on_expect_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """버튼을 기다리는 단계에서 텍스트가 오면 안내 (Gemini 자유 입력으로 넘어가지 않도록 흡수)."""
    await update.message.reply_text("👆 위 버튼에서 선택하거나 ❌ 취소를 눌러 주세요.")


async def on_stale_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """현재 단계와 맞지 않는(지난) 대화 버튼."""
    await update.callback_query.answer("지난 단계의 버튼입니다. 최근 메시지의 버튼을 눌러 주세요.")


def expect_button_handler() -> MessageHandler:
    return MessageHandler(TEXT_INPUT, on_expect_button)


def build_conversation(
    prefix: str, entry_points: list, states: dict, on_cancel, extra_fallbacks=()
) -> ConversationHandler:
    """
    공통 설정의 ConversationHandler: 취소('<prefix>:cancel') → extra_fallbacks → 지난 버튼('<prefix>:*') 순 fallback,
    진행 중 재시작 허용, 15분 타임아웃.
    """
    # 대화 상태를 메시지가 아닌 사용자·채팅 단위로 추적하는 것이 의도이므로 per_message 경고는 무시
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*per_message.*")
        return ConversationHandler(
            entry_points=entry_points,
            states=states,
            fallbacks=[
                CallbackQueryHandler(on_cancel, pattern=rf"^{prefix}:cancel$"),
                *extra_fallbacks,
                CallbackQueryHandler(on_stale_button, pattern=rf"^{prefix}:"),
            ],
            allow_reentry=True,
            conversation_timeout=CONVERSATION_TIMEOUT,
        )
