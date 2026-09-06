from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.handlers.report_handler import (
    chart_command,
    details_command,
    history_command,
    log_command,
    status_command,
)
from bot.handlers.voice_handler import handle_text_transaction, handle_voice_transaction
from config.settings import TELEGRAM_BOT_TOKEN


async def start_command(update, context):
    """/start 명령어: 봇 안내 메시지 (/help 역할)."""
    await update.message.reply_text(
        "👋 자산관리 봇이 준비되었습니다!\n\n"
        "📊 /status  - 포트폴리오 한눈에 보기 (계좌별 요약)\n"
        "📋 /details - 종목별 상세 내역 (계좌별 종목 리스트)\n"
        "📜 /history - 최근 거래 내역 10건\n\n"
        "💬 텍스트나 🎙️ 음성으로 거래를 입력할 수 있어요.\n"
        "예) '토스 삼전 3주 72000원에 매수했어'\n"
        "예) '한투 연금저축에 배당금 2만원 입금'"
    )


def create_bot_app():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN이 .env에 설정되지 않았습니다.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # /start
    app.add_handler(CommandHandler("start", start_command))
    # 조회 명령어
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("details", details_command))
    app.add_handler(CommandHandler("log", log_command))
    app.add_handler(CommandHandler("chart", chart_command))
    app.add_handler(CommandHandler("history", history_command))

    # 음성 수신
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_transaction))

    # 텍스트 수신 (명령어 제외 일반 텍스트)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_transaction))

    return app
