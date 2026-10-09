"""bot/handlers/health_handler.py - /health 봇 상태 점검 (배치 실행·스냅샷·시세 최신성·에러 로그)"""

import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes

from core.health import collect_health, format_health


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.handlers.report_handler import _check_admin

    if not await _check_admin(update):
        return
    try:
        checks = await asyncio.to_thread(collect_health)
        await update.effective_message.reply_text(format_health(checks), parse_mode="HTML")
        logging.info("✅ /health 명령어 응답 완료")
    except Exception as e:
        logging.error(f"❌ /health 처리 실패: {e}", exc_info=True)
        await update.effective_message.reply_text("⚠️ 상태 점검 중 오류가 발생했습니다.")
