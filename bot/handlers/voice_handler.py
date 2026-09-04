import os
from telegram import Update
from telegram.ext import ContextTypes
from core.parser import TransactionParser
from database.repository import AssetRepository

parser = TransactionParser()
repo = AssetRepository()


async def handle_text_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일반 텍스트 메시지로 거래 기록"""
    user_text = update.message.text
    if not user_text:
        return

    # 피드백 전송
    status_msg = await update.message.reply_text("🤖 거래 내용을 분석하고 있습니다...")

    # 1. Gemini로 데이터 파싱
    parsed = parser.parse_text(user_text)
    if not parsed or not parsed.get("account_name") or not parsed.get("action_type"):
        await status_msg.edit_text(
            "⚠️ 거래 내용을 정확히 파악하지 못했습니다. 다시 말씀해 주세요.\n예: '토스 삼전 5주 7만원에 매수'"
        )
        return

    # 2. DB 저장
    success = repo.add_transaction(parsed, raw_memo=user_text)
    if success:
        action_kr = {
            "BUY": "매수",
            "SELL": "매도",
            "DIVIDEND": "배당",
            "DEPOSIT": "입금",
            "WITHDRAW": "출금",
        }.get(parsed["action_type"], parsed["action_type"])

        msg = (
            f"✅ **{action_kr} 기록 완료**\n"
            f"• 날짜: {parsed.get('trans_date')}\n"
            f"• 계좌: {parsed.get('account_name')}\n"
            f"• 종목: {parsed.get('ticker_name')} ({parsed.get('ticker_code') or '티커미정'})\n"
            f"• 수량: {parsed.get('quantity', 0):,.2f}주\n"
            f"• 단가: {parsed.get('unit_price', 0):,.0f}원\n"
            f"• 총금액: {parsed.get('total_amount', 0):,.0f}원"
        )
        await status_msg.edit_text(msg, parse_mode="Markdown")
    else:
        await status_msg.edit_text("❌ DB 저장 중 오류가 발생했습니다.")


async def handle_voice_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """음성 메시지(.ogg)로 거래 기록"""
    voice = update.message.voice
    if not voice:
        return

    status_msg = await update.message.reply_text("🎙️ 음성을 듣고 분석하는 중입니다...")

    temp_path = f"temp_voice_{update.effective_user.id}.ogg"
    try:
        # 1. 텔레그램 서버에서 음성 파일 다운로드
        voice_file = await voice.get_file()
        await voice_file.download_to_drive(temp_path)

        # 2. Gemini 멀티모달 파싱
        parsed = parser.parse_audio(temp_path)
        if not parsed or not parsed.get("account_name"):
            await status_msg.edit_text(
                "⚠️ 음성 내용을 제대로 파악하지 못했습니다. 다시 말씀해 주세요."
            )
            return

        # 3. DB 저장
        success = repo.add_transaction(parsed, raw_memo="[음성입력]")
        if success:
            action_kr = {
                "BUY": "매수",
                "SELL": "매도",
                "DIVIDEND": "배당",
                "DEPOSIT": "입금",
                "WITHDRAW": "출금",
            }.get(parsed["action_type"], parsed["action_type"])

            msg = (
                f"✅ **음성 {action_kr} 기록 완료**\n"
                f"• 계좌: {parsed.get('account_name')}\n"
                f"• 종목: {parsed.get('ticker_name')} ({parsed.get('ticker_code') or '티커미정'})\n"
                f"• 총금액: {parsed.get('total_amount', 0):,.0f}원"
            )
            await status_msg.edit_text(msg, parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ DB 저장 실패")

    except Exception as e:
        await status_msg.edit_text(f"❌ 음성 처리 오류: {e}")
    finally:
        # 임시 오디오 파일 삭제
        if os.path.exists(temp_path):
            os.remove(temp_path)
