"""bot/handlers/menu_handler.py - /start 메인 메뉴 버튼 및 메뉴 콜백 라우팅"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

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


# 하단 고정 키보드 버튼 텍스트 ('거래 입력'은 거래 입력 대화의 진입점에서 처리)
KB_STATUS = "📊 자산 요약"
KB_LIVE = "⚡ 실시간"
KB_PNL = "📈 손익"
KB_TRADE = "✍️ 거래 입력"
KB_MENU = "☰ 메뉴"

# 하단 키보드 조회 버튼 → MENU_ACTIONS 키 ('menu'는 전체 메뉴 표시)
REPLY_KEYBOARD_ACTIONS = {
    KB_STATUS: "status",
    KB_LIVE: "live",
    KB_PNL: "pnl",
    KB_MENU: "menu",
}


def reply_keyboard() -> ReplyKeyboardMarkup:
    """자주 쓰는 기능을 상시 노출하는 하단 고정 키보드."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(KB_STATUS), KeyboardButton(KB_LIVE), KeyboardButton(KB_PNL)],
            [KeyboardButton(KB_TRADE), KeyboardButton(KB_MENU)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


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


async def reply_keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """하단 고정 키보드 조회 버튼 처리.

    거래 입력 대화·Gemini 자유 입력보다 먼저(group -1) 받아 처리하고 이후 핸들러로 넘기지 않는다.
    거래 입력 도중에 눌러도 진행 중인 단계는 그대로 유지된다.
    """
    key = REPLY_KEYBOARD_ACTIONS.get(update.message.text)
    logging.info(f"⚡ 하단 키보드 버튼 수신: {key}")
    if key == "menu":
        await update.message.reply_text("☰ 전체 메뉴", reply_markup=main_menu_keyboard())
    elif key in MENU_ACTIONS:
        await MENU_ACTIONS[key](update, context)
    raise ApplicationHandlerStop
