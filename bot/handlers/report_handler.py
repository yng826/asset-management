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


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repo = AssetRepository()
    transactions = repo.get_recent_transactions(limit=10)

    message = "📜 *최근 거래 내역 (10건)*\n\n"
    if not transactions:
        message += "최근 거래 내역이 없습니다.\n"
    else:
        for tx in transactions:
            trans_date = tx["trans_date"]
            account = tx["account_name"]
            ticker = tx["ticker_name"]
            action_type = tx["action_type"]
            quantity = tx["quantity"]
            unit_price = tx["unit_price"]
            total_amount = tx["total_amount"]
            memo = tx["memo"]

            # 액션 타입에 따라 다른 이모지 및 포맷 적용
            if action_type == "BUY":
                action_emoji = "⬆️ 매수"
                amount_str = f"*{total_amount:,.0f}원*"
            elif action_type == "SELL":
                action_emoji = "⬇️ 매도"
                amount_str = f"*{total_amount:,.0f}원*"
            elif action_type == "DIVIDEND":
                action_emoji = "💰 배당"
                amount_str = f"*{total_amount:,.0f}원*"
            elif action_type == "DEPOSIT":
                action_emoji = "➕ 입금"
                amount_str = f"*{total_amount:,.0f}원*"
            elif action_type == "WITHDRAW":
                action_emoji = "➖ 출금"
                amount_str = f"*{total_amount:,.0f}원*"
            else:
                action_emoji = action_type
                amount_str = f"*{total_amount:,.0f}원*"

            message += (
                f"🗓️ {trans_date} [{account}]\n"
                f"  {action_emoji} {ticker} {quantity:,.2f}주 @ {unit_price:,.0f}원 = {amount_str}\n"
            )
            if memo:
                message += f"  _메모: {memo}_"
            message += "\n"

    await update.message.reply_text(message, parse_mode="Markdown")
