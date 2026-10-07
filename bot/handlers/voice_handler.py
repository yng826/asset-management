import logging
import os
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from core.parser import TransactionParser
from database.repository import AssetRepository

logger = logging.getLogger(__name__)
parser = TransactionParser()
repo = AssetRepository()


async def handle_text_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """일반 텍스트 메시지로 거래 기록 (다건 지원)"""
    user_text = update.message.text
    logger.info(f"💬 거래 텍스트 수신: {user_text}")
    if not user_text:
        return

    # 피드백 전송
    status_msg = await update.message.reply_text("🤖 거래 내용을 분석하고 있습니다...")

    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. Gemini로 데이터 파싱 (리스트 반환)
    parsed_list = parser.parse_text(user_text, reference_date=today_str)
    if not parsed_list:
        logger.warning(f"⚠️ 거래 파싱 실패 결과 비어있음 (원문: {user_text})")
        await status_msg.edit_text(
            "⚠️ 거래 내용을 정확히 파악하지 못했습니다. 다시 말씀해 주세요.\n예: '토스 삼전 5주 7만원에 매수'"
        )
        return

    success_count = 0
    details = []

    action_kr_map = {
        "BUY": "매수",
        "SELL": "매도",
        "DIVIDEND": "배당",
        "DEPOSIT": "입금",
        "WITHDRAW": "출금",
    }

    # 2. DB 저장 (순회)
    for parsed in parsed_list:
        if not parsed.get("account_name") or not parsed.get("action_type"):
            continue

        success = repo.add_transaction(parsed, raw_memo=user_text)
        if success:
            success_count += 1
            action_type = parsed.get("action_type")
            action_kr = action_kr_map.get(action_type, action_type)
            currency = parsed.get("currency") or "KRW"
            curr_unit = "$" if currency == "USD" else "원"

            qty = parsed.get("quantity")
            total = parsed.get("total_amount") or 0.0

            # 수량 표시 방어 (배당/입출금 등 None 또는 0일 때 대응)
            qty_part = f"수량: {qty:,.2f}주 | " if qty is not None else ""

            details.append(
                f"• [{action_kr}] {parsed.get('account_name')} | "
                f"{parsed.get('ticker_name')} ({parsed.get('ticker_code') or '티커미정'}) | "
                f"{qty_part}총액: {total:,.2f}{curr_unit} ({parsed.get('trans_date')})"
            )

    if success_count > 0:
        msg = f"✅ **총 {success_count}건의 거래 기록 완료**\n" + "\n".join(details)
        await status_msg.edit_text(msg, parse_mode="Markdown")
    else:
        await status_msg.edit_text("❌ DB 저장 중 오류가 발생하거나 유효한 거래 정보가 없습니다.")


async def handle_voice_transaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """음성 메시지(.ogg)로 거래 기록 (다건 지원)"""
    voice = update.message.voice
    if not voice:
        return

    status_msg = await update.message.reply_text("🎙️ 음성을 듣고 분석하는 중입니다...")

    temp_path = f"temp_voice_{update.effective_user.id}.ogg"
    try:
        # 1. 텔레그램 서버에서 음성 파일 다운로드
        voice_file = await voice.get_file()
        await voice_file.download_to_drive(temp_path)

        today_str = datetime.now().strftime("%Y-%m-%d")

        # 2. Gemini 멀티모달 파싱 (리스트 반환)
        parsed_list = parser.parse_audio(temp_path, reference_date=today_str)
        if not parsed_list:
            await status_msg.edit_text("⚠️ 음성 내용을 제대로 파악하지 못했습니다. 다시 말씀해 주세요.")
            return

        success_count = 0
        details = []

        action_kr_map = {
            "BUY": "매수",
            "SELL": "매도",
            "DIVIDEND": "배당",
            "DEPOSIT": "입금",
            "WITHDRAW": "출금",
        }

        # 3. DB 저장 (순회)
        for parsed in parsed_list:
            if not parsed.get("account_name") or not parsed.get("action_type"):
                continue

            success = repo.add_transaction(parsed, raw_memo="[음성입력]")
            if success:
                success_count += 1
                action_type = parsed.get("action_type")
                action_kr = action_kr_map.get(action_type, action_type)
                currency = parsed.get("currency", "KRW")
                curr_unit = "$" if currency == "USD" else "원"

                details.append(
                    f"• [{action_kr}] {parsed.get('account_name')} | "
                    f"{parsed.get('ticker_name')} ({parsed.get('ticker_code') or '티커미정'}) | "
                    f"총액: {parsed.get('total_amount', 0):,.2f}{curr_unit} ({parsed.get('trans_date')})"
                )

        if success_count > 0:
            msg = f"✅ **음성 거래 총 {success_count}건 기록 완료**\n" + "\n".join(details)
            await status_msg.edit_text(msg, parse_mode="Markdown")
        else:
            await status_msg.edit_text("❌ DB 저장 실패")

    except Exception as e:
        await status_msg.edit_text(f"❌ 음성 처리 오류: {e}")
    finally:
        # 임시 오디오 파일 삭제
        if os.path.exists(temp_path):
            os.remove(temp_path)
