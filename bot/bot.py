import logging
import os

from telegram import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)
from telegram.error import TelegramError
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from bot.commands import BOT_COMMANDS
from bot.handlers.alert_handler import alert_callback, alert_command
from bot.handlers.health_handler import health_command
from bot.handlers.menu_handler import (
    REPLY_KEYBOARD_ACTIONS,
    main_menu_keyboard,
    menu_callback,
    reply_keyboard,
    reply_keyboard_handler,
)
from bot.handlers.rebalance_handler import target_command
from bot.handlers.recon_handler import build_recon_conversation
from bot.handlers.report_breakdown_handler import breakdown_command
from bot.handlers.report_handler import (
    chart_callback,
    chart_command,
    details_command,
    history_command,
    live_callback,
    live_command,
    log_command,
    pnl_callback,
    pnl_command,
    status_command,
)
from bot.handlers.trade_input_handler import build_trade_conversation, on_stale_button
from bot.handlers.voice_handler import handle_text_transaction, handle_voice_transaction
from bot.weekly_report import weekly_command
from config.settings import ADMIN_USER_ID, TELEGRAM_BOT_TOKEN


async def post_init(application):
    """봇 기동 시 텔레그램 UI 명령어 팝업 자동 동기화"""
    commands = [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS]
    await application.bot.set_my_commands(commands)

    # 기본(default) 목록 하나만 쓰도록, 우선순위가 더 높은 범위별 목록(과거 수동 등록분)은 제거
    scopes = [
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeAllChatAdministrators(),
    ]
    for chat_id in {ADMIN_USER_ID, os.getenv("TELEGRAM_CHAT_ID", "").strip("'\"")}:
        if str(chat_id).lstrip("-").isdigit() and int(chat_id) != 0:
            scopes.append(BotCommandScopeChat(int(chat_id)))
    for scope in scopes:
        try:
            await application.bot.delete_my_commands(scope=scope)
        except TelegramError as e:
            logging.warning(f"⚠️ 범위별 명령어 목록 제거 실패 ({scope.type}): {e}")


async def start_command(update, context):
    """/start 명령어: 봇 안내 메시지 (/help 역할)."""
    logging.info(f"⚡ /start 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    await update.message.reply_text(
        "👋 자산관리 봇이 준비되었습니다!\n\n"
        "하단 버튼으로 자주 쓰는 기능을 바로 실행할 수 있어요.\n"
        "(기간 직접 지정: /pnl 14, /pnl 2w, /chart stack 6m)\n\n"
        "💬 텍스트나 🎙️ 음성으로 거래를 입력할 수 있어요.\n"
        "예) '토스 삼전 3주 72000원에 매수했어'\n"
        "예) '한투 연금저축에 배당금 2만원 입금'",
        reply_markup=reply_keyboard(),
    )
    # 메시지당 키보드는 하나만 붙일 수 있으므로 전체 메뉴(인라인 버튼)는 별도 메시지로 전송
    await update.message.reply_text("☰ 전체 메뉴", reply_markup=main_menu_keyboard())
    logging.info("✅ /start 명령어 응답 완료")


def create_bot_app():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN이 .env에 설정되지 않았습니다.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # 하단 고정 키보드 조회 버튼 — 거래 입력 대화·Gemini 자유 입력보다 먼저 가로채기 (group -1)
    app.add_handler(
        MessageHandler(filters.Text(list(REPLY_KEYBOARD_ACTIONS)), reply_keyboard_handler),
        group=-1,
    )

    # 버튼 단계 거래 입력 (/trade, 메뉴 '거래 입력') — 진행 중 텍스트를 먼저 받도록 일반 텍스트 핸들러보다 앞에 등록
    app.add_handler(build_trade_conversation())
    # 예수금 잔액 대사 (/recon, 메뉴 '잔액 대사') — 실제 잔액 텍스트 입력을 받으므로 같은 위치에 등록
    app.add_handler(build_recon_conversation())

    # /start
    app.add_handler(CommandHandler("start", start_command))
    # 조회 명령어
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("details", details_command))
    app.add_handler(CommandHandler("live", live_command))
    app.add_handler(CommandHandler("pnl", pnl_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("breakdown", breakdown_command))

    app.add_handler(CommandHandler("chart", chart_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("weekly", weekly_command))
    app.add_handler(CommandHandler("target", target_command))
    app.add_handler(CommandHandler("alert", alert_command))
    app.add_handler(CommandHandler("health", health_command))

    # 인라인 버튼 콜백 (callback_data 접두어로 분기)
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu:"))
    app.add_handler(CallbackQueryHandler(pnl_callback, pattern=r"^pnl:"))
    app.add_handler(CallbackQueryHandler(live_callback, pattern=r"^live:"))
    app.add_handler(CallbackQueryHandler(chart_callback, pattern=r"^chart:"))
    app.add_handler(CallbackQueryHandler(alert_callback, pattern=r"^alert:"))
    app.add_handler(CallbackQueryHandler(on_stale_button, pattern=r"^tr:"))

    # 음성 수신
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_transaction))

    # 텍스트 수신 (명령어 제외 일반 텍스트)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_transaction))

    return app
