import html
import logging
import re
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import ADMIN_USER_ID
from core.calculator import (
    enrich_holdings_with_prices,
    get_latest_fx_rate,
    get_latest_prices_map,
)
from core.formatter import build_status_chunks, build_status_summary
from core.live_tracker import get_live_tracker_status
from database.repository import AssetRepository


def _get_safe_admin_id():
    """ADMIN_USER_ID에서 따옴표를 정규화하고 정수형으로 변환합니다."""
    return int(str(ADMIN_USER_ID).strip("'\"")) if ADMIN_USER_ID else None


async def _check_admin(update: Update) -> bool:
    """사용자가 관리자인지 확인합니다."""
    admin_id = _get_safe_admin_id()
    if update.effective_user.id != admin_id:
        logging.warning(
            f"🚫 비관리자 접근 시도: {update.effective_user.id} (요청 명령어: {update.message.text if update.message else ''})"
        )
        await update.message.reply_text("🚫 관리자 전용 명령어입니다.")
        return False
    return True


async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pnl 명령어: 최근 N일간 일별 손익 추이 조회."""
    logging.info(f"⚡ /pnl 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    if not await _check_admin(update):
        return

    # 파라미터 처리
    days = 7
    if context.args:
        try:
            days = int(context.args[0])
        except ValueError:
            await update.message.reply_text("⚠️ 올바른 숫자를 입력하세요. 예: /pnl 14")
            return

    try:
        repo = AssetRepository()
        # 최근 N일치 데이터 조회 (repository는 DESC 정렬해서 주므로 그대로 활용)
        all_history = repo.get_daily_pnl_history()
        target_history = all_history[:days]

        if not target_history:
            await update.message.reply_text("📉 최근 조회 가능한 손익 데이터가 없습니다.")
            return

        from core.formatter import format_pnl_daily

        message = format_pnl_daily(target_history)
        await update.message.reply_text(message, parse_mode="HTML")
        logging.info(f"✅ /pnl 명령어 응답 완료 (최근 {days}일)")

    except Exception as e:
        logging.error(f"❌ /pnl 명령어 실행 실패: {e}")
        await update.message.reply_text("⚠️ 손익 리포트 생성 중 오류가 발생했습니다.")


def _load_enriched_holdings() -> list:
    """DB → holdings → 가격 매핑 → enriched dict 리스트 적재 헬퍼.
    - /status, /details 핸들러 공통 사용.
    - holdings 집계 + daily_prices 조회 + USD/KRW 환율 매핑 + 평가 산출을 한 번에 수행.
    """
    logging.info("📊 보유 종목 및 가격 매핑 적재 시작...")
    repo = AssetRepository()
    logging.info("📊 현재 보유 종목 조회 중...")
    holdings = repo.get_current_holdings()
    logging.info(f"📊 현재 보유 종목 수: {len(holdings)}")
    price_map = get_latest_prices_map()
    logging.info(f"📊 최근 종가 매핑 수: {len(price_map)}")
    fx_rate = get_latest_fx_rate()
    logging.info(f"📊 USD/KRW 환율: {fx_rate}")
    return enrich_holdings_with_prices(holdings, price_map, fx_rate)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status 명령어: 포트폴리오 요약 조회."""
    logging.info(f"⚡ /status 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    if not await _check_admin(update):
        return

    try:
        enriched = _load_enriched_holdings()
        message = build_status_summary(enriched)
    except Exception as e:
        logging.error(f"❌ /status 요약 생성 실패: {e}")
        message = (
            "📊 <b>포트폴리오 한눈에 보기</b>\n\n"
            "리포트를 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n"
            "<i>(데이터는 실제와 다를 수 있습니다.)</i>"
        )

    if len(message) > 4000:
        message = message[:3950] + "\n\n...(이하 생략)..."

    await update.message.reply_text(message, parse_mode="HTML")
    logging.info("✅ /status 명령어 응답 완료")


async def details_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/details 명령어: 계좌별 세부 보유 종목 조회."""
    logging.info(f"⚡ /details 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    if not await _check_admin(update):
        return

    try:
        enriched = _load_enriched_holdings()
        logging.info(f"📊 /details 상세 리포트 생성: {len(enriched)} 종목")
        # ⚠️ context 인자를 넘기지 않고 단독 호출하여 기본 chunk_size를 사용
        chunks = build_status_chunks(enriched)
    except Exception as e:
        logging.error(f"❌ /details 상세 리포트 생성 실패: {e}")
        chunks = [
            "📊 <b>보유 종목 상세</b>\n\n"
            "리포트를 생성하는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.\n\n"
            "<i>(데이터는 실제와 다를 수 있습니다.)</i>"
        ]

    for chunk in chunks:
        if len(chunk) > 4000:
            chunk = chunk[:3950] + "\n\n...(이하 생략)..."
        await update.message.reply_text(chunk, parse_mode="HTML")

    logging.info("✅ /details 명령어 응답 완료")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.info(f"⚡ /history 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
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
                message += f"  (메모: {html.escape(memo)})\n"

    if len(message) > 4000:
        message = message[:3950] + "\n\n...(이하 생략)..."

    await update.message.reply_text(message)
    logging.info("✅ /history 명령어 응답 완료")


async def _handle_comparison_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """자산 수익률 vs 지수 비교 차트 로직 격리."""
    period = period_args[0].lower() if period_args else "all"

    today = datetime.now()
    match = re.match(r"^(\d+)([dwmy])$", period)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)

        if unit == "d":
            delta = timedelta(days=amount)
        elif unit == "w":
            delta = timedelta(weeks=amount)
        elif unit == "m":
            delta = timedelta(days=amount * 30)
        elif unit == "y":
            delta = timedelta(days=amount * 365)
        else:
            delta = timedelta(days=365)  # Fallback

        start_date = (today - delta).strftime("%Y-%m-%d")
    else:
        # 기본값
        start_date = "2026-05-01"

    end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    from bot.chart_renderer import render_comparison_chart
    from core.calculator import get_performance_comparison

    try:
        benchmark_tickers = ["KS11", "KQ11", "US500", "KRW-BTC"]
        data = get_performance_comparison(start_date, end_date, benchmark_tickers=benchmark_tickers)
        if not data["dates"]:
            await update.message.reply_text("📉 해당 기간에 사용할 수 있는 스냅샷 데이터가 없습니다.")
            logging.info("✅ /chart 명령어 응답 완료 (데이터 없음)")
            return

        buf = render_comparison_chart(data)
        await update.message.reply_photo(photo=buf, caption="📈 수익률 비교 차트")
        logging.info("✅ /chart 명령어 응답 완료")
    except Exception as e:
        logging.error(f"❌ /chart 생성 실패: {e}")
        await update.message.reply_text("⚠️ 차트 생성 중 오류가 발생했습니다.")


async def _handle_allocation_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """자산 배분 비중 차트 로직 격리."""
    period = period_args[0].lower() if period_args else "all"

    today = datetime.now()
    match = re.match(r"^(\d+)([dwmy])$", period)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)

        if unit == "d":
            delta = timedelta(days=amount)
        elif unit == "w":
            delta = timedelta(weeks=amount)
        elif unit == "m":
            delta = timedelta(days=amount * 30)
        elif unit == "y":
            delta = timedelta(days=amount * 365)
        else:
            delta = timedelta(days=365)  # Fallback

        start_date = (today - delta).strftime("%Y-%m-%d")
    else:
        # 기본값
        start_date = "2026-05-01"

    end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    from bot.chart_renderer import render_allocation_chart
    from core.calculator import get_asset_allocation_history

    try:
        data = get_asset_allocation_history(start_date, end_date)
        if not data["dates"] or not data["categories"]:
            await update.message.reply_text(
                "📉 해당 기간에 사용할 수 있는 자산 배분 스냅샷 데이터가 없습니다."
            )
            logging.info("✅ /chart alloc 명령어 응답 완료 (데이터 없음)")
            return

        buf = render_allocation_chart(data)
        await update.message.reply_photo(photo=buf, caption="📈 자산 배분 누적 면적 차트 (/chart alloc)")
        logging.info("✅ /chart alloc 명령어 응답 완료")
    except Exception as e:
        logging.error(f"❌ /chart alloc 생성 실패: {e}")
        await update.message.reply_text("⚠️ 자산 배분 차트 생성 중 오류가 발생했습니다.")


async def chart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /chart 명령어:
    - /chart alloc: 자산군별 비중(%) 정규화 area chart
    - /chart stack: 자산군별 절대금액 스택 바 차트
    """
    if not await _check_admin(update):
        return

    command = context.args[0] if context.args else None

    if command == "alloc":
        from bot.chart_renderer import render_allocation_chart
        from core.calculator import get_asset_allocation_history

        data = get_asset_allocation_history()
        buf = render_allocation_chart(data)
        await update.message.reply_photo(photo=buf)

    elif command in ["stack", "bar"]:
        await _handle_stack_bar_chart(update, context, context.args[1:])

    else:
        # 기존 로직 (비교 차트)
        from bot.chart_renderer import render_comparison_chart
        from core.calculator import get_portfolio_vs_benchmark_performance

        data = get_portfolio_vs_benchmark_performance()
        buf = render_comparison_chart(data)
        await update.message.reply_photo(photo=buf)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/log 명령어: 관리자 전용 최신 로그 출력."""
    import os
    from collections import deque

    logging.info(f"⚡ /log 명령어 수신: {update.effective_user.id if update.effective_user else 'None'}")
    if not await _check_admin(update):
        return

    log_path = "logs/app.log"
    if not os.path.exists(log_path):
        await update.message.reply_text("📂 로그 파일을 찾을 수 없습니다.")
        logging.info("✅ /log 명령어 응답 완료 (로그 파일 없음)")
        return

    try:
        # 파일 내용을 한꺼번에 읽지 않고 마지막 30줄만 안전하게 읽음
        with open(log_path, encoding="utf-8") as f:
            last_lines = deque(f, maxlen=30)

            message = "📜 최신 로그 (마지막 30줄):\n\n```\n" + "".join(last_lines) + "\n```"

            # 텔레그램 메시지 길이 제한 처리
            if len(message) > 4000:
                message = message[:3950] + "\n...(이하 생략)...```"

            await update.message.reply_text(message, parse_mode="Markdown")
            logging.info("✅ /log 명령어 응답 완료")

    except Exception as e:
        logging.error(f"❌ /log 명령어 실행 실패: {e}")
        await update.message.reply_text(f"⚠️ 로그 읽기 실패: {e}")


async def live_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/live 명령어: 멀티 자산 실시간 현황."""
    logging.info(
        f"⚡ /live 명령어 수신: {update.effective_user.id if update.effective_user else 'None'} "
        f"(args: {context.args})"
    )

    # 인자 분기 처리
    arg = context.args[0].lower() if context.args else "all"
    asset_type = "all"
    title = "전체 포트폴리오"

    if arg in ["kr", "stock"]:
        asset_type = "kr"
        title = "국내 주식"
    elif arg == "us":
        asset_type = "us"
        title = "미국 주식"
    elif arg in ["crypto", "coin"]:
        asset_type = "crypto"
        title = "가상자산"
    elif arg != "all":
        await update.message.reply_text("⚠️ 지원하지 않는 자산군입니다. (all, kr, us, crypto)")
        return

    try:
        data = get_live_tracker_status(asset_type=asset_type)
        if not data or not data["assets"]:
            await update.message.reply_text(f"📉 보유 중인 {title} 자산이 없습니다.")
            return

        assets_msg = ""
        for asset in data["assets"]:
            ticker = html.escape(asset["ticker"])
            icon = "🔺" if asset["diff_amount"] >= 0 else "\U0001f53b"
            # 미국 주식일 경우 달러 표기
            if asset.get("is_us"):
                price_str = f"${asset['price']:,.2f}"
                diff_str = f"{icon} ${abs(asset['diff_amount']):,.2f}"
            else:
                price_str = f"{asset['price']:,.0f}원"
                diff_str = f"{icon} {asset['diff_amount']:+,.0f}원"

            assets_msg += f"• {ticker}: {price_str} ({diff_str}, {asset['diff_rate']:+.2f}%)\n"

        icon = "🔺" if data["total_diff"] >= 0 else "\U0001f53b"
        total_eval_str = f"{data['total_eval']:,.0f}원"
        if asset_type == "us" and data.get("fx_rate"):
            total_eval_str = f"${data['total_eval'] / data['fx_rate']:,.2f} (약 {data['total_eval']:,.0f}원)"

        message = (
            f"⚡ <b>{title} 실시간 현황 (Live)</b>\n"
            f"기준: {data['timestamp']}\n"
            f"---------------------------------\n"
            f"{assets_msg}"
            f"---------------------------------\n"
            f"총 평가액: <b>{total_eval_str}</b>\n"
            f"실시간 변동: {icon} <b>{data['total_diff']:+,.0f}원 ({data['total_diff_rate']:+.2f}%)</b>\n"
            f"<i>(전일 종가 대비 실시간 추산)</i>"
        )

        await update.message.reply_text(message, parse_mode="HTML")
        logging.info(f"✅ /live {asset_type} 명령어 응답 완료")

    except Exception as e:
        logging.error(f"❌ /live 명령어 실행 실패: {e}")
        await update.message.reply_text("⚠️ 실시간 현황 조회 중 오류가 발생했습니다.")


async def send_status_report(bot, chat_id: str | int, title: str = "", full_report: bool = True) -> None:
    """스케줄러/배치 전용 리포트 발송 래퍼 함수"""
    try:
        from core.formatter import build_status_chunks, build_status_summary

        enriched = _load_enriched_holdings()

        if full_report:
            # 일일 결산 등 전체 상세 리포트 (청크 분할 발송)
            chunks = build_status_chunks(enriched)
            if title:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"📢 <b>[{title}]</b>",
                    parse_mode="HTML",
                )
            for chunk in chunks:
                if len(chunk) > 4000:
                    chunk = chunk[:3950] + "\n\n...(이하 생략)..."
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
        else:
            # 오전 브리핑 등 요약 리포트
            summary = build_status_summary(enriched)
            header = f"📢 <b>[{title}]</b>\n\n" if title else ""
            msg = header + summary
            if len(msg) > 4000:
                msg = msg[:3950] + "\n\n...(이하 생략)..."
            await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")

        logging.info(f"✅ 정기 리포트 발송 완료: {title}")
    except Exception as e:
        logging.error(f"❌ 정기 리포트 발송 중 오류: {e}", exc_info=True)


async def _handle_stack_bar_chart(
    update: Update, context: ContextTypes.DEFAULT_TYPE, period_args: list
) -> None:
    """자산군별 절대금액 스택 바 차트 로직 격리."""
    period = period_args[0].lower() if period_args else "all"

    today = datetime.now()
    match = re.match(r"^(\d+)([dwmy])$", period)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)

        if unit == "d":
            delta = timedelta(days=amount)
        elif unit == "w":
            delta = timedelta(weeks=amount)
        elif unit == "m":
            delta = timedelta(days=amount * 30)
        elif unit == "y":
            delta = timedelta(days=amount * 365)
        else:
            delta = timedelta(days=365)

        start_date = (today - delta).strftime("%Y-%m-%d")
    else:
        start_date = "2026-05-01"

    end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    from bot.chart_renderer import render_stack_bar_chart
    from core.calculator import get_asset_stack_eval_history

    try:
        data = get_asset_stack_eval_history(start_date, end_date)
        if not data["dates"] or not data["values"]:
            await update.message.reply_text("📉 해당 기간에 사용할 수 있는 스냅샷 데이터가 없습니다.")
            return

        buf = render_stack_bar_chart(data)
        await update.message.reply_photo(
            photo=buf, caption="📈 자산군별 절대금액 스택 바 차트 (/chart stack)"
        )
    except Exception as e:
        logging.error(f"❌ /chart stack 생성 실패: {e}")
        await update.message.reply_text("⚠️ 차트 생성 중 오류가 발생했습니다.")
