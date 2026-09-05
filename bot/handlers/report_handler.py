from telegram import Update
from telegram.ext import ContextTypes

from core.calculator import (
    enrich_holdings_with_prices,
    get_latest_fx_rate,
    get_latest_prices_map,
)
from core.formatter import build_status_chunks, build_status_summary
from database.repository import AssetRepository


def _load_enriched_holdings() -> list:
    """DB → holdings → 가격 매핑 → enriched dict 리스트 적재 헬퍼.

    - /status, /details 핸들러 공통 사용.
    - holdings 집계 + daily_prices 조회 + USD/KRW 환율 매핑 + 평가 산출을 한 번에 수행.
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()
    price_map = get_latest_prices_map()
    fx_rate = get_latest_fx_rate()
    return enrich_holdings_with_prices(holdings, price_map, fx_rate)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status 명령어: 모바일 한 화면에 들어오는 '한 페이지 요약 리포트'.

    - 데이터 소스:
        1) transactions 원장 -> get_current_holdings()
        2) daily_prices 최신 종가 매핑
        3) USD/KRW 환율 매핑 (해외주식)
        4) 미수집 종목은 평단가 fallback
    - 각 계좌: 계좌명 + (매수/평가) + 손익 3줄 압축
    - 최하단 [전체 포트폴리오 요약] 블록
    - 텔레그램 4096자 한도 내 단일 메시지 (보통 1,500자 이내)
    - parse_mode 미지정: 일반 텍스트로 전송 (Markdown 파싱 오류 회피)
    - 자세한 종목 리스트가 필요하면 /details 사용
    """
    try:
        enriched = _load_enriched_holdings()
        message = build_status_summary(enriched)
    except Exception as e:
        print(f"❌ /status 요약 생성 실패: {e}")
        message = (
            "📊 포트폴리오 한눈에 보기\n\n"
            "리포트를 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n"
            "(데이터는 실제와 다를 수 있습니다.)"
        )

    if len(message) > 4000:
        # 안전 마진 (실제로는 거의 발생하지 않음)
        message = message[:3950] + "\n\n...(이하 생략)..."
    await update.message.reply_text(message)


async def details_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/details 명령어: 종목별 상세 리포트 (계좌 그룹 + 종목 리스트 + 소계).

    - 메시지가 길어지면 2,400자 안전 마진으로 여러 메시지 분할 전송.
    - 전체 요약 블록은 반드시 마지막 메시지에 포함.
    - parse_mode 미지정: 일반 텍스트로 전송.
    """
    try:
        enriched = _load_enriched_holdings()
        chunks = build_status_chunks(enriched)
    except Exception as e:
        print(f"❌ /details 상세 리포트 생성 실패: {e}")
        chunks = [
            "📊 보유 종목 상세\n\n"
            "리포트를 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n"
            "(데이터는 실제와 다를 수 있습니다.)"
        ]

    for i, chunk in enumerate(chunks):
        if len(chunk) > 4000:
            chunk = chunk[:3950] + "\n\n...(이하 생략)..."
        await update.message.reply_text(chunk)


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repo = AssetRepository()
    transactions = repo.get_recent_transactions(limit=10)

    message = "📜 최근 거래 내역 (10건)\n\n"
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

            if action_type == "BUY":
                action_emoji = "⬆️ 매수"
            elif action_type == "SELL":
                action_emoji = "⬇️ 매도"
            elif action_type == "DIVIDEND":
                action_emoji = "💰 배당"
            elif action_type == "DEPOSIT":
                action_emoji = "➕ 입금"
            elif action_type == "WITHDRAW":
                action_emoji = "➖ 출금"
            else:
                action_emoji = action_type

            message += (
                f"🗓️ {trans_date} [{account}]\n"
                f"  {action_emoji} {ticker} {quantity:,.2f}주 @ {unit_price:,.0f}원 = {total_amount:,.0f}원\n"
            )
            if memo:
                message += f"  (메모: {memo})\n"

    if len(message) > 4000:
        message = message[:3950] + "\n\n...(이하 생략)..."

    await update.message.reply_text(message)
