from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters
from config.settings import TELEGRAM_BOT_TOKEN
from bot.handlers.voice_handler import handle_text_transaction, handle_voice_transaction


async def start_command(update, context):
    await update.message.reply_text(
        "👋 자산관리 봇이 준비되었습니다!\n\n"
        "💬 텍스트나 🎙️ 음성으로 편하게 말씀해 주세요.\n"
        "예) '토스 삼전 3주 72000원에 매수했어'\n"
        "예) '한투 연금저축에 배당금 2만원 입금'"
    )


def create_bot_app():
    if not TELEGRAM_BOT_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN이 .env에 설정되지 않았습니다.")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # /start
    app.add_handler(CommandHandler("start", start_command))

    # 음성 수신
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_transaction))

    # 텍스트 수신 (명령어 제외 일반 텍스트)
    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_transaction)
    )

    return app
