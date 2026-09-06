import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from core.calculator import (
    enrich_holdings_with_prices,
    get_latest_fx_rate,
    get_latest_prices_map,
)
from core.formatter import (
    build_status_chunks,
    build_status_summary,
    save_today_snapshot,
)
from core.price_fetcher import fetch_and_save_benchmarks
from database.repository import AssetRepository

# 로깅 설정
logger = logging.getLogger(__name__)


async def _send_report(application: Application, chat_id: str, title: str, full_report: bool = False):
    """
    자산 평가 및 리포트 발송
    """
    try:
        holdings = AssetRepository().get_current_holdings()
        price_map = get_latest_prices_map()
        fx_rate = get_latest_fx_rate()

        enriched_holdings = enrich_holdings_with_prices(holdings, price_map, fx_rate)

        # 전체 요약 리포트 (build_status_summary 사용)
        summary_content = build_status_summary(enriched_holdings)
        # MarkdownV2 형식으로 제목을 추가하고 본문과 결합
        summary_message = f"*{title}*\n\n{summary_content}"
        await application.bot.send_message(chat_id=chat_id, text=summary_message, parse_mode="MarkdownV2")
        logger.info(f"[{title}] 전체 요약 리포트 발송 완료")

        if full_report:
            # 계좌별 상세 리포트 (build_status_chunks 사용)
            detail_chunks = build_status_chunks(enriched_holdings)
            # 각 청크는 이미 완전한 메시지이므로, 제목을 별도로 추가할 필요 없음.
            for chunk in detail_chunks:
                await application.bot.send_message(chat_id=chat_id, text=chunk, parse_mode="MarkdownV2")
                await asyncio.sleep(0.5)  # 메시지 전송 간격
            logger.info(f"[{title}] 계좌별 상세 리포트 발송 완료 (총 {len(detail_chunks)}개 청크)")

    except Exception as e:
        logger.error(f"리포트 발송 중 오류 발생: {e}", exc_info=True)
        if application and chat_id:
            # 오류 메시지 발송 시 Markdown 파싱 문제 방지를 위해 None으로 설정
            await application.bot.send_message(chat_id=chat_id, text=f"⚠️ 오류 발생: {e}", parse_mode=None)


async def morning_briefing(application: Application, chat_id: str):
    """
    해외 주식 마감 및 환율, 펀드 기준가 반영 오전 브리핑 (평일 10:30)
    """
    logger.info("오전 브리핑 시작...")
    await _send_report(application, chat_id, "오전 브리핑: 해외 주식, 환율, 펀드 반영", full_report=False)
    logger.info("오전 브리핑 완료.")


async def daily_closing_report(application: Application, chat_id: str):
    """
    국내 주식 마감 반영 및 일일 전체 자산 종합 결산 리포트 (평일 16:00)
    """
    logger.info("일일 결산 리포트 시작...")
    await _send_report(application, chat_id, "일일 결산: 국내 주식 마감 및 전체 자산", full_report=True)
    logger.info("일일 결산 리포트 완료.")


async def weekly_closing_report(application: Application, chat_id: str):
    """
    금요일 밤 미국장 마감 반영 및 주간 자산 결산 리포트 (토요일 10:00)
    """
    logger.info("주간 결산 리포트 시작...")
    await _send_report(application, chat_id, "주간 결산: 미국장 마감 및 주간 자산", full_report=True)
    logger.info("주간 결산 리포트 완료.")


def setup_scheduler(application: Application, chat_id: str) -> AsyncIOScheduler:
    """
    APScheduler를 설정하고 모든 스케줄된 작업을 추가합니다.
    """
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 평일(월~금) 10:30: 해외 주식(미국장 애프터마켓 마감), 환율, 펀드 기준가 반영 및 오전 브리핑
    scheduler.add_job(
        morning_briefing,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=30),
        args=[application, chat_id],
        id="morning_briefing_job",
    )

    # 평일(월~금) 16:00: 국내 주식(정규장 마감) 가격 반영 및 일일 전체 자산 종합 결산 리포트
    scheduler.add_job(
        daily_closing_report,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        args=[application, chat_id],
        id="daily_closing_report_job",
    )

    # 토요일 10:00: 금요일 밤 미국장 마감 반영 및 주간 자산 결산 리포트
    scheduler.add_job(
        weekly_closing_report,
        CronTrigger(day_of_week="sat", hour=10, minute=0),
        args=[application, chat_id],
        id="weekly_closing_report_job",
    )
    # 벤치마크 지수/환율 일일 수집 (평일 17:00, 장 마감 후)
    scheduler.add_job(
        fetch_and_save_benchmarks,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0),
        id="fetch_benchmarks_job",
    )

    # 일일 총자산 스냅샷 집계 (평일 17:10)
    scheduler.add_job(
        save_today_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=10),
        id="save_snapshot_job",
    )

    logger.info("모든 스케줄된 작업이 등록되었습니다.")
    return scheduler
