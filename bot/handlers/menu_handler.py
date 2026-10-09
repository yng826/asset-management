"""bot/handlers/menu_handler.py - /start 메인 메뉴 버튼 및 메뉴 콜백 라우팅"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.report_breakdown_handler import breakdown_command
from bot.handlers.report_handler import (
    chart_command,
    details_command,
    history_command,
    live_command,
    pnl_command,
    status_command,
)

# 메뉴 키 → 실행할 명령어 핸들러 (인자 없이 호출한 것과 동일하게 동작)
MENU_ACTIONS = {
    "status": status_command,
    "details": details_command,
    "live": live_command,
    "pnl": pnl_command,
    "chart": chart_command,
    "breakdown": breakdown_command,
    "history": history_command,
}


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 자산 요약", callback_data="menu:status"),
                InlineKeyboardButton("📋 종목 상세", callback_data="menu:details"),
            ],
            [
                InlineKeyboardButton("⚡ 실시간", callback_data="menu:live"),
                InlineKeyboardButton("📈 일별 손익", callback_data="menu:pnl"),
            ],
            [
                InlineKeyboardButton("📉 차트", callback_data="menu:chart"),
                InlineKeyboardButton("🥧 자산군 비중", callback_data="menu:breakdown"),
            ],
            [
                InlineKeyboardButton("🧾 최근 거래", callback_data="menu:history"),
                InlineKeyboardButton("✍️ 거래 입력", callback_data="tr:new"),
            ],
        ]
    )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """메인 메뉴 버튼 콜백 (menu:<key>): 해당 명령어를 새 메시지로 응답 (메뉴는 그대로 유지)."""
    query = update.callback_query
    await query.answer()

    key = query.data.split(":", 1)[1]
    action = MENU_ACTIONS.get(key)
    if action is None:
        return

    logging.info(f"⚡ 메뉴 버튼 수신: {key}")
    await action(update, context)
