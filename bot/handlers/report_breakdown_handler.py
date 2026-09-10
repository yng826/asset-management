import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.report_handler import _check_admin
from core.calculator import summarize_total
from core.formatter import format_asset_breakdown
from database.repository import AssetRepository


async def breakdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/breakdown 명령어: 자산군별 비중 조회."""
    logging.info(
        f"⚡ /breakdown 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}"
    )
    if not await _check_admin(update):
        return

    try:
        repo = AssetRepository()
        summary = repo.get_latest_asset_class_summary()

        # 총 평가액 계산 (기존 로직 활용)
        # 1. holdings 로딩 (calculator + repository 조합)
        # 2. summarize_total() 사용
        from core.calculator import enrich_holdings_with_prices, get_latest_fx_rate, get_latest_prices_map

        holdings = repo.get_current_holdings()
        price_map = get_latest_prices_map()
        fx_rate = get_latest_fx_rate()
        enriched = enrich_holdings_with_prices(holdings, price_map, fx_rate)
        total_data = summarize_total(enriched)
        total_eval = total_data.get("valuation_amount", 0.0)

        message = format_asset_breakdown(summary, total_eval)
        await update.message.reply_text(message, parse_mode="HTML")
        logging.info("✅ /breakdown 명령어 응답 완료")

    except Exception as e:
        logging.error(f"❌ /breakdown 명령어 실행 실패: {e}")
        await update.message.reply_text("⚠️ 자산군 비중 리포트 생성 중 오류가 발생했습니다.")
