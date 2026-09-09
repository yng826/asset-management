"""
core/detector/anomaly.py
- 장전 갭출발, 장중 고점 대비 급락, 시간외 단일가 변동 등 이상징후 감지 모듈
"""

import html
import logging
from datetime import datetime

import FinanceDataReader as fdr
import pyupbit
from telegram.ext import Application

from database.repository import AssetRepository

logger = logging.getLogger(__name__)

# 동일 종목 중복 알림 방지용 캐시 (알림 시각 기록: {"TICKER_EVENT": datetime})
_ALERT_COOLDOWN_CACHE: dict[str, datetime] = {}
COOLDOWN_SECONDS = 3600  # 동일 종목 경보는 1시간 동안 재발송 방지


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
    간밤 해외 증시 마감 기반 당일 갭출발 징후 감시 (±2.0% 이상 시 텔레그램 발송)
    """
    try:
        sp_df = fdr.DataReader("US500", datetime.now().date())
        if sp_df.empty:
            return

        latest_return = ((sp_df["Close"].iloc[-1] - sp_df["Open"].iloc[0]) / sp_df["Open"].iloc[0]) * 100

        if abs(latest_return) >= 2.0:
            alert_key = f"PREMARKET_GAP_{datetime.now().strftime('%Y%m%d')}"
            if _can_alert(alert_key):
                direction = "🔺 갭상승" if latest_return > 0 else "🔻 갭하락"
                msg = (
                    f"⚠️ <b>[장전 이상징후 경보] 오늘 증시 {direction} 출발 유력</b>\n\n"
                    f"• 간밤 미국 S&P 500: <b>{latest_return:+.2f}%</b>\n"
                    "• 국내 개장(09:00) 시 보유 종목 변동성에 유의하세요."
                )
                await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
                logger.info(f"장전 프리마켓 갭 경보 발송 완료: {latest_return:+.2f}%")
    except Exception as e:
        logger.error(f"check_premarket_anomaly 오류: {e}")


async def check_intraday_anomaly(application: Application, chat_id: str):
    """
    [정규장 감시 - 평일 09:05~15:25]
    보유 종목의 당일 고점 대비 급락 감시 (주식 -2.5%, 코인 -3.5%)
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
                df = pyupbit.get_ohlcv(code, interval="day", count=1)
                if df is not None and not df.empty:
                    high_p = float(df["high"].iloc[-1])
                    curr_p = float(df["close"].iloc[-1])
                    if high_p > 0:
                        dd = ((curr_p - high_p) / high_p) * 100
                        if dd <= -3.5:
                            key = f"INTRADAY_DD_{code}"
                            if _can_alert(key):
                                anomalies.append(
                                    f"• <b>{html.escape(code)}</b>: 당일 최고가({high_p:,.0f}원) 대비 "
                                    f"<b>{dd:.2f}%</b> 급락 중 (현재가: {curr_p:,.0f}원)"
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
                        if dd <= -2.5:
                            key = f"INTRADAY_DD_{code}"
                            if _can_alert(key):
                                anomalies.append(
                                    f"• <b>{html.escape(str(code))}</b>: 장중 고가({high_p:,.0f}원) 대비 "
                                    f"<b>{dd:.2f}%</b> 급락 (현재가: {curr_p:,.0f}원)"
                                )
            except Exception:
                continue

        if anomalies:
            msg = (
                "🚨 <b>[장중 이상징후 경보] 고점 대비 급락 감지</b>\n\n"
                + "\n".join(anomalies)
                + "\n\n<i>차익 실현 매물 출회 및 수급 변동성을 확인하세요.</i>"
            )
            await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            logger.info(f"장중 이상징후 경보 발송 완료: {len(anomalies)}건")
    except Exception as e:
        logger.error(f"check_intraday_anomaly 오류: {e}")


async def check_aftermarket_anomaly(application: Application, chat_id: str):
    """
    [시간외 단일가 감시 - 평일 16:15~18:00]
    정규장 종가 대비 시간외 시세 급변(±3.0% 이상) 종목 감시 (스켈레톤)
    """
    try:
        pass
    except Exception as e:
        logger.error(f"check_aftermarket_anomaly 오류: {e}")
