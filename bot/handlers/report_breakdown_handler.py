import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers.report_handler import _check_admin
from core.formatter import format_asset_class_report, get_closed_pnl, price_date_header_lines
from database.repository import AssetRepository


async def breakdown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/breakdown 명령어: 자산군별 비중 조회.

    /status 와 동일한 평가 결과(enrich_holdings_with_prices + 계좌별 예수금)를 자산군 단위로 집계.
    (기존 v_latest_asset_breakdown 뷰는 해외주식 원화 환산·매도 차감·예수금이 빠져 있어 사용하지 않음)
    """
    logging.info(
        f"⚡ /breakdown 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}"
    )
    if not await _check_admin(update):
        return

    try:
        from core.calculator import (
            enrich_holdings_with_prices,
            get_account_cash_map,
            get_latest_fx_rate,
            get_latest_prices_map,
            summarize_asset_classes,
            summarize_class_pnl,
        )

        repo = AssetRepository()
        holdings = repo.get_current_holdings()
        price_map = get_latest_prices_map()
        fx_rate = get_latest_fx_rate()
        enriched = enrich_holdings_with_prices(holdings, price_map, fx_rate)
        cash_by_account = get_account_cash_map(fx_rate)

        closed_pnl = get_closed_pnl()
        class_pnl = summarize_class_pnl(closed_pnl["snapshot_date"]) if closed_pnl else {}
        summary = summarize_asset_classes(enriched, cash_by_account, class_pnl)
        header_lines = price_date_header_lines(enriched)
        if fx_rate and fx_rate.get("rate"):
            header_lines.append(f"환율: {float(fx_rate['rate']):,.2f}원/USD ({fx_rate.get('price_date')})")

        message = format_asset_class_report(summary, header_lines, closed_pnl)
        await update.effective_message.reply_text(message, parse_mode="HTML")
        logging.info("✅ /breakdown 명령어 응답 완료")

    except Exception as e:
        logging.error(f"❌ /breakdown 명령어 실행 실패: {e}")
        await update.effective_message.reply_text("⚠️ 자산군 비중 리포트 생성 중 오류가 발생했습니다.")
