import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from config.settings import COLLECTOR_EXTERNAL
from core.collection import (
    check_and_auto_heal_missing_snapshots,
    collect_closing,
    collect_morning,
    collect_probe,
    collect_weekend,
    record_batch,
)
from core.detector import (
    check_aftermarket_anomaly,
    check_crypto_anomaly,
    check_intraday_anomaly,
    check_premarket_anomaly,
    check_us_anomaly,
)
from core.fetcher import collect_crypto_prices
from core.ticker_master import ensure_ticker_master, sync_ticker_master
from database.repository import AssetRepository

logger = logging.getLogger(__name__)


async def _send_report(application: Application, chat_id: str, title: str, full_report: bool = True):
    """스케줄러 공용 리포트 발송 래퍼"""
    from bot.handlers.report_handler import send_status_report

    await send_status_report(application.bot, chat_id, title, full_report=full_report)


# 하위 호환: 감사 로그·자가 치유는 core.collection 으로 이동 (수집기와 공용)
_record_batch = record_batch


async def morning_briefing(application: Application, chat_id: str):
    """오전 브리핑 (평일 09:05): 해외 주식, 환율, 펀드, 코인 수집 및 발송

    - 코인 전일 일봉이 09:00 에 확정된 뒤 실행해 확정 종가 반영

    - 국내 종가도 재수집해 전일 16:00 수집값을 확정 종가로 덮어쓴 뒤, 최근 결산 스냅샷 재계산
    - COLLECTOR_EXTERNAL: 수집·감사 로그는 k3s 수집기(collector morning, 09:02)가 맡고 발송만 수행
    """
    error = None
    if not COLLECTOR_EXTERNAL:
        check_and_auto_heal_missing_snapshots()
        error = collect_morning()

    await _send_report(application, chat_id, "오전 브리핑: 해외 자산 및 환율/펀드", full_report=False)
    if not COLLECTOR_EXTERNAL:
        _record_batch(AssetRepository(), "morning_0845", "오전 브리핑 및 시세 수집 완료", error)


async def daily_closing_report(application: Application, chat_id: str):
    """국내 정규장 마감 및 일일 결산 (평일 16:00, 국내 휴장일 포함)

    - COLLECTOR_EXTERNAL: 수집·스냅샷·감사 로그는 k3s 수집기(collector closing, 15:55)가 맡고 발송만 수행
    """
    error = None
    if not COLLECTOR_EXTERNAL:
        check_and_auto_heal_missing_snapshots()
        error = collect_closing()

    await _send_report(application, chat_id, "일일 결산: 정규장 마감 및 전체 자산", full_report=True)
    if not COLLECTOR_EXTERNAL:
        _record_batch(AssetRepository(), "closing_1600", "일일 결산 및 시세 수집 완료", error)


async def weekend_closing_report(application: Application, chat_id: str):
    """주말 결산 (토·일 09:05): 오전 브리핑 수집 + 당일 스냅샷 저장

    - 주말 스냅샷 시세(국내·미국 금요일 종가, 코인 전일 일봉)는 09:00 이면 모두 확정되므로 16:00 까지 기다리지 않음
    - 토요일: 금요일 밤 미국장 마감을 반영해 금요일 스냅샷도 재계산
    - 감사 로그는 평일 결산과 같은 closing_1600 으로 기록 (당일 스냅샷 확정 판단 기준)
    - COLLECTOR_EXTERNAL: 수집·스냅샷·감사 로그는 k3s 수집기(collector weekend, 09:02)가 맡고 발송만 수행
    """
    error = None
    if not COLLECTOR_EXTERNAL:
        check_and_auto_heal_missing_snapshots()
        error = collect_weekend()

    await _send_report(application, chat_id, "주말 결산: 글로벌 마감 및 전체 자산", full_report=True)
    if not COLLECTOR_EXTERNAL:
        _record_batch(
            AssetRepository(),
            "closing_1600",
            "주말 결산 및 시세 수집 완료",
            error,
            judge_as="closing_weekend",
        )


async def weekly_performance_report(application: Application, chat_id: str):
    """일요일 08:00: 주간 성과 리포트 (토요일 결산 스냅샷 = 금요일 미국장 마감 반영분까지)"""
    from bot.weekly_report import send_weekly_report

    logger.info("주간 성과 리포트 발송 시작...")
    try:
        sent = await send_weekly_report(application.bot, chat_id)
        logger.info(f"주간 성과 리포트 발송 {'완료' if sent else '생략 (데이터 부족)'}")
    except Exception as e:
        logger.error(f"주간 성과 리포트 발송 실패: {e}", exc_info=True)


async def probe_morning_data(application: Application, chat_id: str, probe_tag: str = "probe_0830"):
    """08:30 사전 수집 테스트: 메시지 발송 없이 펀드/미국주식 인입 여부만 DB에 기록"""
    repo = AssetRepository()
    collect_probe(probe_tag)

    # 감사 로그 테이블에 'probe_0830'으로 적재 건수 기록
    repo.record_batch_audit_log(probe_tag, message=f"{probe_tag} 조기 수집 가용성 점검")


def setup_scheduler(application: Application, chat_id: str) -> AsyncIOScheduler:
    """APScheduler를 설정하고 정기 배치 및 이상징후 감시 작업을 등록합니다."""
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # COLLECTOR_EXTERNAL: 시세 수집·스냅샷 전용 작업은 k3s 수집기(deploy/k3s)가 실행하므로 등록하지 않음
    if COLLECTOR_EXTERNAL:
        logger.info("COLLECTOR_EXTERNAL=1: 수집 전용 작업(사전 점검·코인 일봉·종목 마스터 동기화) 미등록")
    else:
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
        CronTrigger(day_of_week="mon-fri", hour=9, minute=5),
        args=[application, chat_id],
        id="morning_briefing_job",
    )
    scheduler.add_job(
        daily_closing_report,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        args=[application, chat_id],
        id="daily_closing_report_job",
    )
    scheduler.add_job(
        weekend_closing_report,
        CronTrigger(day_of_week="sat,sun", hour=9, minute=5),
        args=[application, chat_id],
        id="weekend_closing_report_job",
    )
    # 1-1. [매일 09:01] 가상자산 전일 일봉 종가 확정분 수집 (업비트 일봉 09:00 KST 마감)
    if not COLLECTOR_EXTERNAL:
        scheduler.add_job(
            collect_crypto_prices,
            CronTrigger(day_of_week="*", hour=9, minute=1),
            kwargs={"verbose": False},
            id="crypto_daily_close_job",
            replace_existing=True,
        )

    # 1-2. 종목 마스터 (버튼 거래 입력 종목 검색용): 기동 시 비어 있으면 1회 적재 + [일요일 07:00] 주간 동기화
    scheduler.add_job(ensure_ticker_master, id="ticker_master_ensure_job", replace_existing=True)
    if not COLLECTOR_EXTERNAL:
        scheduler.add_job(
            sync_ticker_master,
            CronTrigger(day_of_week="sun", hour=7, minute=0),
            id="ticker_master_sync_job",
            replace_existing=True,
        )

    # 1-3. [일요일 08:00] 주간 성과 리포트 (TWR·벤치마크·낙폭·종목 기여·환율 효과 + 차트 2장)
    scheduler.add_job(
        weekly_performance_report,
        CronTrigger(day_of_week="sun", hour=8, minute=0),
        args=[application, chat_id],
        id="weekly_performance_report_job",
        replace_existing=True,
    )

    # 1-4. [매일 16:10] 목표 비중 이탈 알림 (당일 결산 스냅샷 기준, 목표 미설정 시 무동작)
    from core.rebalance import check_rebalance_drift

    scheduler.add_job(
        check_rebalance_drift,
        CronTrigger(day_of_week="*", hour=16, minute=10),
        args=[application, chat_id],
        id="rebalance_drift_job",
        replace_existing=True,
    )

    # 1-5. [매일 16:30] 봇 상태 점검 (배치·스냅샷·시세 최신성·에러 로그, 문제 있을 때만 알림)
    from core.health import check_health_and_alert

    scheduler.add_job(
        check_health_and_alert,
        CronTrigger(day_of_week="*", hour=16, minute=30),
        args=[application, chat_id],
        id="health_check_job",
        replace_existing=True,
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
    scheduler.add_job(
        check_us_anomaly,
        CronTrigger(day_of_week="mon-sat", hour="0-6,22-23", minute="*/10"),
        args=[application, chat_id],
        id="us_anomaly_job",
    )
    scheduler.add_job(
        check_crypto_anomaly,
        CronTrigger(minute="*/5"),
        args=[application, chat_id],
        id="crypto_anomaly_job",
    )

    logger.info("모든 정기 스케줄 및 이상징후 감시 체커 등록 완료.")
    return scheduler
