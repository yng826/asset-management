import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from core.calculator import save_today_snapshot
from core.detector import (
    check_aftermarket_anomaly,
    check_intraday_anomaly,
    check_premarket_anomaly,
)
from core.fetcher import (
    collect_crypto_prices,
    collect_fund_prices,
    collect_fx_rate,
    collect_kr_prices,
    collect_us_prices,
)
from database.repository import AssetRepository

logger = logging.getLogger(__name__)


async def _send_report(application: Application, chat_id: str, title: str, full_report: bool = True):
    """스케줄러 공용 리포트 발송 래퍼"""
    from bot.handlers.report_handler import send_status_report

    await send_status_report(application.bot, chat_id, title, full_report=full_report)


async def morning_briefing(application: Application, chat_id: str):
    """오전 브리핑 (평일 10:30): 해외 주식, 환율, 펀드, 코인 수집 및 발송"""
    repo = AssetRepository()
    logger.info("오전 브리핑 시세 수집 시작...")
    try:
        collect_us_prices(verbose=False)
        collect_fx_rate(verbose=False)
        collect_fund_prices(verbose=False)
        collect_crypto_prices(verbose=False)
    except Exception as e:
        logger.error(f"오전 시세 수집 중 오류: {e}", exc_info=True)

    await _send_report(application, chat_id, "오전 브리핑: 해외 자산 및 환율/펀드", full_report=False)
    repo.record_batch_audit_log("morning_1030", message="오전 브리핑 및 시세 수집 완료")


async def daily_closing_report(application: Application, chat_id: str):
    """국내 정규장 마감 및 일일 결산 (매일 16:00, 주말 코인 포함)"""
    repo = AssetRepository()
    logger.info("일일 결산 시세 수집 시작...")
    try:
        collect_kr_prices(verbose=False)
        collect_crypto_prices(verbose=False)
        save_today_snapshot()
    except Exception as e:
        logger.error(f"일일 결산 파이프라인 오류: {e}", exc_info=True)

    await _send_report(application, chat_id, "일일 결산: 정규장 마감 및 전체 자산", full_report=True)
    repo.record_batch_audit_log("closing_1600", message="일일 결산 및 시세 수집 완료")


async def weekly_closing_report(application: Application, chat_id: str):
    """토요일 10:00: 금요일 밤 미국장 마감 반영 및 주간 결산"""
    logger.info("주간 결산 브리핑 시작...")
    try:
        collect_us_prices(verbose=False)
        collect_fx_rate(verbose=False)
    except Exception as e:
        logger.error(f"주간 결산 수집 중 오류: {e}", exc_info=True)

    await _send_report(application, chat_id, "주간 결산: 글로벌 마감 리포트", full_report=True)


def setup_scheduler(application: Application, chat_id: str) -> AsyncIOScheduler:
    """APScheduler를 설정하고 정기 배치 및 이상징후 감시 작업을 등록합니다."""
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 1. 정기 브리핑 및 마감 결산 (리포트 발송)
    scheduler.add_job(
        morning_briefing,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=30),
        args=[application, chat_id],
        id="morning_briefing_job",
    )
    scheduler.add_job(
        daily_closing_report,
        CronTrigger(day_of_week="*", hour=16, minute=0),
        args=[application, chat_id],
        id="daily_closing_report_job",
    )
    scheduler.add_job(
        weekly_closing_report,
        CronTrigger(day_of_week="sat", hour=10, minute=0),
        args=[application, chat_id],
        id="weekly_closing_report_job",
    )

    # 2. 이상징후 상시 감시 체커 (이상 감지 시에만 노티)
    scheduler.add_job(
        check_premarket_anomaly,
        CronTrigger(day_of_week="mon-fri", hour=8, minute="30,35,40,45,50,55"),
        args=[application, chat_id],
        id="premarket_anomaly_job",
    )
    scheduler.add_job(
        check_intraday_anomaly,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5"),
        args=[application, chat_id],
        id="intraday_anomaly_job",
    )
    scheduler.add_job(
        check_aftermarket_anomaly,
        CronTrigger(day_of_week="mon-fri", hour="16-17", minute="15,30,45,59"),
        args=[application, chat_id],
        id="aftermarket_anomaly_job",
    )

    logger.info("모든 정기 스케줄 및 이상징후 감시 체커 등록 완료.")
    return scheduler
