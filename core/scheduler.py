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
    """오전 브리핑 (평일 08:45): 해외 주식, 환율, 펀드, 코인 수집 및 발송"""
    repo = AssetRepository()
    check_and_auto_heal_missing_snapshots()
    logger.info("오전 브리핑 시세 수집 시작...")
    try:
        collect_us_prices(verbose=False)
        collect_fx_rate(verbose=False)
        collect_fund_prices(verbose=False)
        collect_crypto_prices(verbose=False)
    except Exception as e:
        logger.error(f"오전 시세 수집 중 오류: {e}", exc_info=True)

    await _send_report(application, chat_id, "오전 브리핑: 해외 자산 및 환율/펀드", full_report=False)
    repo.record_batch_audit_log("morning_0845", message="오전 브리핑 및 시세 수집 완료")


async def daily_closing_report(application: Application, chat_id: str):
    """국내 정규장 마감 및 일일 결산 (매일 16:00, 주말 코인 포함)"""
    repo = AssetRepository()
    check_and_auto_heal_missing_snapshots()
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


def check_and_auto_heal_missing_snapshots():
    """서버 부팅 시 최근 스냅샷 누락 일자를 감지하여 자동 복구"""
    try:
        from datetime import datetime, timedelta

        from scripts.repair_history import repair

        from database.connection import get_connection

        conn = get_connection()
        if not conn:
            return
        cur = conn.cursor()
        cur.execute("SELECT MAX(snapshot_date) FROM daily_snapshots;")
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row and row[0]:
            last_date = row[0].strftime("%Y-%m-%d") if hasattr(row[0], "strftime") else str(row[0])
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            if last_date < yesterday:
                target_start = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime(
                    "%Y-%m-%d"
                )
                logger.info(f"🛠️ 누락된 과거 스냅샷 감지 ({target_start} ~ {yesterday}), 자동 복구 시작...")
                repair(target_start, yesterday)
    except Exception as e:
        logger.error(f"⚠️ 자가 치유(Auto-heal) 실행 중 오류: {e}")


async def probe_morning_data(application: Application, chat_id: str, probe_tag: str = "probe_0830"):
    """08:30 사전 수집 테스트: 메시지 발송 없이 펀드/미국주식 인입 여부만 DB에 기록"""
    repo = AssetRepository()
    logger.info(f"🔍 [{probe_tag}] 사전 시세 수집 테스트 시작...")
    try:
        collect_us_prices(verbose=False)
        collect_fx_rate(verbose=False)
        collect_fund_prices(verbose=False)
        collect_crypto_prices(verbose=False)
    except Exception as e:
        logger.error(f"{probe_tag} 사전 수집 중 오류: {e}", exc_info=True)

    # 감사 로그 테이블에 'probe_0830'으로 적재 건수 기록
    repo.record_batch_audit_log(probe_tag, message=f"{probe_tag} 조기 수집 가용성 점검")


def setup_scheduler(application: Application, chat_id: str) -> AsyncIOScheduler:
    """APScheduler를 설정하고 정기 배치 및 이상징후 감시 작업을 등록합니다."""
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
    check_and_auto_heal_missing_snapshots()

    # 0. [평일 08:30] 사전 수집 점검 (알림 X, DB 적재 및 로그 기록 O)
    scheduler.add_job(
        probe_morning_data,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=30),
        args=[application, chat_id, "probe_0830"],
        id="probe_0830_job",
        replace_existing=True,
    )
    scheduler.add_job(
        probe_morning_data,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=40),
        args=[application, chat_id, "probe_0840"],
        id="probe_0840_job",
        replace_existing=True,
    )
    scheduler.add_job(
        probe_morning_data,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=50),
        args=[application, chat_id, "probe_0850"],
        id="probe_0850_job",
        replace_existing=True,
    )

    # 1. 정기 브리핑 및 마감 결산 (리포트 발송)
    scheduler.add_job(
        morning_briefing,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=55),
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
