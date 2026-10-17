"""
core/collection.py
- 정기 시세 수집 파이프라인 (텔레그램 발송과 분리된 순수 수집·스냅샷 단계)
- 봇 스케줄러(core.scheduler)와 k3s 수집기(collector)가 같은 순서로 호출한다.
  텔레그램·APScheduler 를 import 하지 않으므로 수집기 이미지에 봇 의존성이 필요 없음.

각 collect_* 함수는 예외를 삼키고 반환한다 (None = 정상). 감사 로그 판정은 record_batch 가 담당.
"""

import logging

from core.calculator import refresh_recent_snapshots, save_today_snapshot
from core.fetcher import (
    collect_crypto_prices,
    collect_fund_prices,
    collect_fx_rate,
    collect_kr_prices,
    collect_us_prices,
    fetch_and_save_benchmarks,
)
from database.repository import AssetRepository

logger = logging.getLogger(__name__)


def check_and_auto_heal_missing_snapshots():
    """최근 스냅샷 누락 일자를 감지하여 자동 복구"""
    try:
        from datetime import datetime, timedelta

        from scripts.repair_history import repair

        from database.connection import fetch_all

        rows = fetch_all("SELECT MAX(snapshot_date) FROM daily_snapshots;")
        row = rows[0] if rows else None

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


def record_batch(
    repo: AssetRepository, batch_name: str, message: str, error: Exception | None, judge_as: str | None = None
) -> None:
    """배치 감사 로그 기록: 상태는 시세 최신성(지수 최신 거래일 기준)으로 판정해 휴장일 오탐 방지.

    - judge_as: 판정 기준 배치명 (기록 이름과 수집 범위가 다를 때, 예: 주말 결산)
    """
    from core.health import judge_batch

    status, note = judge_batch(judge_as or batch_name, error)
    if status != "SUCCESS":
        logger.warning(f"배치 {batch_name} 상태 {status}: {note}")
    repo.record_batch_audit_log(batch_name, message=f"{message} | {note}" if note else message, status=status)


def collect_morning(snapshots: bool = True) -> Exception | None:
    """오전 수집: 해외 주식, 환율, 펀드, 코인, 국내 종가(전일 확정), 지수 + 최근 결산 스냅샷 재계산

    - snapshots=False: 시세만 수집 (수집기 섀도 운영, 스냅샷은 기존 daily_prices 기준이라 건드리지 않음)
    """
    logger.info("오전 브리핑 시세 수집 시작...")
    try:
        collect_us_prices(verbose=False)
        collect_fx_rate(verbose=False)
        collect_fund_prices(verbose=False)
        collect_crypto_prices(verbose=False)
        collect_kr_prices(verbose=False)
        fetch_and_save_benchmarks()
        if snapshots:
            refreshed = refresh_recent_snapshots()
            logger.info(f"최근 결산 스냅샷 재계산: {refreshed}")
    except Exception as e:
        logger.error(f"오전 시세 수집 중 오류: {e}", exc_info=True)
        return e
    return None


def collect_closing(snapshots: bool = True) -> Exception | None:
    """평일 결산 수집: 국내 종가, 코인, 지수 + 당일 스냅샷 저장"""
    logger.info("일일 결산 시세 수집 시작...")
    try:
        collect_kr_prices(verbose=False)
        collect_crypto_prices(verbose=False)
        fetch_and_save_benchmarks()
        if snapshots:
            save_today_snapshot()
    except Exception as e:
        logger.error(f"일일 결산 파이프라인 오류: {e}", exc_info=True)
        return e
    return None


def collect_weekend(snapshots: bool = True) -> Exception | None:
    """주말 결산 수집: 오전 수집 전체 + 최근 스냅샷 재계산 + 당일 스냅샷 저장"""
    logger.info("주말 결산 시세 수집 시작...")
    try:
        collect_us_prices(verbose=False)
        collect_fx_rate(verbose=False)
        collect_fund_prices(verbose=False)
        collect_crypto_prices(verbose=False)
        collect_kr_prices(verbose=False)
        fetch_and_save_benchmarks()
        if snapshots:
            refreshed = refresh_recent_snapshots()
            logger.info(f"최근 결산 스냅샷 재계산: {refreshed}")
            save_today_snapshot()
    except Exception as e:
        logger.error(f"주말 결산 파이프라인 오류: {e}", exc_info=True)
        return e
    return None


def collect_probe(probe_tag: str) -> Exception | None:
    """장전 사전 수집 점검: 펀드/미국주식/환율/코인 인입 여부 확인용 수집"""
    logger.info(f"🔍 [{probe_tag}] 사전 시세 수집 테스트 시작...")
    try:
        collect_us_prices(verbose=False)
        collect_fx_rate(verbose=False)
        collect_fund_prices(verbose=False)
        collect_crypto_prices(verbose=False)
    except Exception as e:
        logger.error(f"{probe_tag} 사전 수집 중 오류: {e}", exc_info=True)
        return e
    return None
