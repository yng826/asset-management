from telegram import Update
from telegram.ext import ContextTypes
from database.repository import AssetRepository


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repo = AssetRepository()
    holdings = repo.get_current_holdings()

    message = "📊 *현재 보유 종목 현황*\n\n"
    if not holdings:
        message += "보유 중인 종목이 없습니다.\n"
    else:
        total_asset_value = 0.0
        for holding in holdings:
            account = holding["account_name"]
            ticker = holding["ticker_name"]
            quantity = holding["quantity"]
            avg_price = holding["avg_price"]

            # TODO: 현재가 연동하여 평가 금액 및 손익 계산
            current_price = avg_price  # 임시
            valuation_amount = current_price * quantity
            total_asset_value += valuation_amount

            message += (
                f"*{ticker}* ({account})\n"
                f"  수량: {quantity:,.2f}주\n"
                f"  평단가: {avg_price:,.0f}원\n"
                f"  평가금액: {valuation_amount:,.0f}원\n\n"
            )

    # TODO: 현금 잔고도 추가해야 함
    message += f"*총 자산 평가액: {total_asset_value:,.0f}원*\n\n"
    message += "_데이터는 실제와 다를 수 있습니다._"

    await update.message.reply_text(message, parse_mode="Markdown")
