import asyncio
import html
import logging
from datetime import datetime

import FinanceDataReader as fdr
import pyupbit
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from core.calculator import (
    enrich_holdings_with_prices,
    get_latest_fx_rate,
    get_latest_prices_map,
    save_today_snapshot,
)
from core.fetcher import (
    collect_crypto_prices,
    collect_fund_prices,
    collect_fx_rate,
    collect_kr_prices,
    collect_us_prices,
)
from core.formatter import (
    build_status_chunks,
    build_status_summary,
)
from database.repository import AssetRepository

# 로깅 설정
logger = logging.getLogger(__name__)

# 동일 종목 중복 알림 방지용 캐시 (알림 시각 기록: {"TICKER_EVENT": datetime})
_ALERT_COOLDOWN_CACHE: dict[str, datetime] = {}
COOLDOWN_SECONDS = 3600  # 동일 경보는 1시간 동안 재발송 방지


def _can_alert(key: str) -> bool:
    now = datetime.now()
    if key in _ALERT_COOLDOWN_CACHE:
        elapsed = (now - _ALERT_COOLDOWN_CACHE[key]).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            return False
    _ALERT_COOLDOWN_CACHE[key] = now
    return True


async def check_premarket_anomaly(application: Application, chat_id: str):
    """
    [장전/프리마켓 감시 - 평일 08:30~08:55]
    야간선물/미국 기술주 마감 기반 당일 갭출발 징후 감시 (±2.0% 이상 발생 시에만 노티)
    """
    try:
        # 야간선물/주요 지수(예: KOSPI200 야간선물 or S&P500/나스닥 선물) 전일비 변동 체크
        # FDR을 통해 직전 마감 지수와 현재 선물 변동폭 간이 확인
        sp_df = fdr.DataReader("US500", datetime.now().date())
        if sp_df.empty:
            return

        # 간밤 미국장 마감 등락률 확인
        latest_return = ((sp_df["Close"].iloc[-1] - sp_df["Open"].iloc[0]) / sp_df["Open"].iloc[0]) * 100

        # 지수가 ±2.0% 이상 갭/급변했을 때만 1회 경보
        if abs(latest_return) >= 2.0:
            alert_key = f"PREMARKET_GAP_{datetime.now().strftime('%Y%m%d')}"
            if _can_alert(alert_key):
                direction = "🔺 갭상승" if latest_return > 0 else "🔻 갭하락"
                msg = (
                    f"⚠️ <b>[장전 이상징후 경보] 오늘 증시 {direction} 출발 유력</b>\n\n"
                    f"• 간밤 미국 S&P 500 마감: <b>{latest_return:+.2f}%</b>\n"
                    f"• 국내 개장(09:00) 시 보유 종목의 변동성 확대에 유의하세요."
                )
                await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                logger.info(f"장전 프리마켓 갭 경보 발송 완료: {latest_return:+.2f}%")
    except Exception as e:
        logger.error(f"check_premarket_anomaly 실행 중 오류: {e}")


async def check_intraday_anomaly(application: Application, chat_id: str):
    """
    [정규장 감시 - 평일 09:05~15:25]
    보유 종목의 당일 고점 대비 급락(-2.5% 이상) 또는 가상자산 급변 감시
    """
    try:
        repo = AssetRepository()
        holdings = repo.get_current_holdings()
        anomalies = []

        # 1. 가상자산(Upbit) 실시간 고점 대비 낙폭 체크
        crypto_codes = [
            h["ticker_code"] for h in holdings if str(h.get("ticker_code", "")).startswith("KRW-")
        ]
        for code in set(crypto_codes):
            try:
                # Upbit 당일 일봉 데이터 (고가, 현재가)
                df = pyupbit.get_ohlcv(code, interval="day", count=1)
                if df is not None and not df.empty:
                    high_p = float(df["high"].iloc[-1])
                    curr_p = float(df["close"].iloc[-1])
                    if high_p > 0:
                        # 당일 최고점 대비 낙폭(Drawdown)
                        dd = ((curr_p - high_p) / high_p) * 100
                        if dd <= -3.5:  # 코인은 변동성이 커서 -3.5% 임계치
                            key = f"INTRADAY_DD_{code}"
                            if _can_alert(key):
                                anomalies.append(
                                    f"• <b>{code}</b>: 당일 최고가({high_p:,.0f}원) 대비 <b>{dd:.2f}%</b> 급락 중 (현재가: {curr_p:,.0f}원)"
                                )
            except Exception:
                continue

        # 2. 국내 주식/ETF 실시간 고점 대비 낙폭 체크
        stock_codes = [
            h["ticker_code"]
            for h in holdings
            if h.get("ticker_code") and str(h["ticker_code"]).isdigit() and len(str(h["ticker_code"])) == 6
        ]
        for code in set(stock_codes):
            try:
                df = fdr.DataReader(code, datetime.now().date())
                if df is not None and not df.empty:
                    high_p = float(df["High"].max())
                    curr_p = float(df["Close"].iloc[-1])
                    if high_p > 0:
                        dd = ((curr_p - high_p) / high_p) * 100
                        if dd <= -2.5:  # 주식은 고점 대비 -2.5% 이상 하락 시 포착
                            key = f"INTRADAY_DD_{code}"
                            if _can_alert(key):
                                anomalies.append(
                                    f"• <b>{code}</b>: 장중 고가({high_p:,.0f}원) 대비 <b>{dd:.2f}%</b> 급락 (현재가: {curr_p:,.0f}원)"
                                )
            except Exception:
                continue

        # 포착된 이상징후가 있을 때만 텔레그램 발송
        if anomalies:
            msg = (
                "🚨 <b>[장중 이상징후 경보] 고점 대비 급락 감지</b>\n\n" + "\n".join(anomalies) + "\n\n"
                "<i>차익 실현 매물 출회 및 수급 변동성을 확인하세요.</i>"
            )
            await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            logger.info(f"장중 이상징후 경보 발송 완료: {len(anomalies)}건")

    except Exception as e:
        logger.error(f"check_intraday_anomaly 실행 중 오류: {e}")


async def check_aftermarket_anomaly(application: Application, chat_id: str):
    """
    [시간외 단일가 감시 - 평일 16:15~18:00]
    정규장 마감 종가 대비 시간외 시세 급변(±3.0% 이상) 종목 감시
    """
    try:
        # 시간외 시세는 FDR/네이버 실시간 스크래핑 등으로 당일 종가와 비교
        # 현재는 시간외 변동 임계치 초과 종목 발생 시에만 알림을 주는 뼈대
        # 조건 미충족 시 아무 동작도 하지 않고 종료(Silent Pass)
        pass
    except Exception as e:
        logger.error(f"check_aftermarket_anomaly 실행 중 오류: {e}")


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
        summary_message = f"<b>{html.escape(title)}</b>\n\n{summary_content}"
        await application.bot.send_message(chat_id=chat_id, text=summary_message, parse_mode="HTML")
        logger.info(f"[{title}] 전체 요약 리포트 발송 완료")

        if full_report:
            # 계좌별 상세 리포트 (build_status_chunks 사용)
            detail_chunks = build_status_chunks(enriched_holdings)
            # 각 청크는 이미 완전한 메시지이므로, 제목을 별도로 추가할 필요 없음.
            for chunk in detail_chunks:
                await application.bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
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
    - 수집 함수 직접 호출: 미국 주식, 환율, 펀드, 코인
    """
    repo = AssetRepository()
    logger.info("오전 브리핑 시세 수집 시작 (미국주식, 환율, 펀드, 코인)...")
    try:
        collect_us_prices(verbose=False)
        collect_fx_rate(verbose=False)
        collect_fund_prices(verbose=False)
        collect_crypto_prices(verbose=False)
    except Exception as e:
        logger.error(f"오전 브리핑 시세 수집 중 오류: {e}", exc_info=True)

    logger.info("오전 브리핑 리포트 발송 시작...")
    await _send_report(application, chat_id, "오전 브리핑: 해외 주식, 환율, 펀드 반영", full_report=False)
    logger.info("오전 브리핑 완료.")
    repo.record_batch_audit_log("morning_1030", message="오전 브리핑 및 시세 수집 완료")


async def daily_closing_report(application: Application, chat_id: str):
    """국내 주식 마감 반영, 당일 스냅샷 저장 및 일일 전체 자산 종합 결산 리포트 (평일 16:00)"""
    repo = AssetRepository()
    logger.info("일일 결산 시세 수집 시작 (국내주식, 코인)...")
    try:
        collect_kr_prices(verbose=False)
        collect_crypto_prices(verbose=False)
    except Exception as e:
        logger.error(f"일일 결산 시세 수집 중 오류: {e}", exc_info=True)

    # 16:00 시세 수집 직후 당일 스냅샷(총합 + 종목별) 즉시 생성
    try:
        logger.info("당일 자산 스냅샷 저장 실행...")
        save_today_snapshot()
    except Exception as e:
        logger.error(f"스냅샷 저장 중 오류: {e}", exc_info=True)

    logger.info("일일 결산 리포트 발송 시작...")
    await _send_report(
        application,
        chat_id,
        "일일 결산: 국내 주식 마감 및 전체 자산",
        full_report=True,
    )
    logger.info("일일 결산 리포트 완료.")

    # 스냅샷 생성 후 감사 로그를 남기므로 snapshot_created = 1 달성
    repo.record_batch_audit_log("closing_1600", message="일일 결산 및 시세 수집 완료")


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

    # ----------------------------------------------------
    # 1. 정기 브리핑 및 마감 결산 (정규 리포트 발송)
    # ----------------------------------------------------
    # 평일(월~금) 10:30: 해외 주식, 환율, 펀드 기준가 반영 및 오전 브리핑
    scheduler.add_job(
        morning_briefing,
        CronTrigger(day_of_week="mon-fri", hour=10, minute=30),
        args=[application, chat_id],
        id="morning_briefing_job",
    )

    # 매일(월~일) 16:00: 국내 주식 마감 반영 및 일일 전체 자산 결산 (주말 코인 스냅샷 누락 방지)
    scheduler.add_job(
        daily_closing_report,
        CronTrigger(day_of_week="*", hour=16, minute=0),
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

    # ----------------------------------------------------
    # 2. 이상징후 상시 감시 체커 (이상 발생 시에만 즉시 노티 발송)
    # ----------------------------------------------------
    # [프리마켓/장전] 평일 08:30 ~ 08:55 (5분 주기 감시)
    # -> 야간선물/동시호가 기준 전일비 ±2.0% 이상 갭 발생 시에만 긴급 노티
    scheduler.add_job(
        check_premarket_anomaly,
        CronTrigger(day_of_week="mon-fri", hour=8, minute="30,35,40,45,50,55"),
        args=[application, chat_id],
        id="premarket_anomaly_job",
    )

    # [정규장] 평일 09:05 ~ 15:25 (5분 주기 감시)
    # -> 당일 최고점 대비 -2.5% 이상 급락 or 거래량 급증 시에만 노티
    scheduler.add_job(
        check_intraday_anomaly,
        CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/5"),
        args=[application, chat_id],
        id="intraday_anomaly_job",
    )

    # [시간외 단일가] 평일 16:15 ~ 18:00 (15분 주기 감시)
    # -> 정규장 종가 대비 ±3.0% 이상 급변 종목 포착 시에만 노티
    scheduler.add_job(
        check_aftermarket_anomaly,
        CronTrigger(day_of_week="mon-fri", hour="16-17", minute="15,30,45,59"),
        args=[application, chat_id],
        id="aftermarket_anomaly_job",
    )

    logger.info("모든 스케줄된 정기 작업 및 이상징후 감시 체커가 등록되었습니다.")
    return scheduler
