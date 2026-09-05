from telegram import Update
from telegram.ext import ContextTypes

from core.calculator import build_status_chunks
from database.repository import AssetRepository


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/status` 명령어: 보유 종목/계좌별 평가액 + 손익 + 수익률 출력.

    - 데이터 소스:
        1) transactions 원장 -> get_current_holdings() (잔고 집계)
        2) daily_prices 최신 종가 매핑
        3) 미수집 종목은 평단가 fallback
    - 메시지가 길어지면 텔레그램 4096자 한도 내에서 여러 메시지로 자동 분할되어 전송.
    - 전체 포트폴리오 요약 블록은 반드시 마지막 메시지에 포함됨.
    """
    try:
        chunks = build_status_chunks()
    except Exception as e:
        print(f"❌ /status 리포트 생성 실패: {e}")
        chunks = [
            "📊 *현재 보유 종목 현황*\n\n"
            "리포트를 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n"
            "_데이터는 실제와 다를 수 있습니다._"
        ]

    for i, chunk in enumerate(chunks):
        # 안전 마진: 청크 빌더가 이미 4000자로 자르지만, 텔레그램 API 오류 대비
        if len(chunk) > 4000:
            chunk = chunk[:3950] + "\n\n...(이하 생략)..."
        await update.message.reply_text(chunk, parse_mode="Markdown")


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

    # history 도 안전 마진
    if len(message) > 4000:
        message = message[:3950] + "\n\n...(이하 생략)..."

    await update.message.reply_text(message, parse_mode="Markdown")
