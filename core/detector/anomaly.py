"""
core/detector/anomaly.py
- 장전 갭출발, 보유 종목(국내/미국/코인) 전일 대비 급락 및 당일 고점 대비 낙폭 등 이상징후 감지 모듈
- 기준값은 DB(detector_settings)에서 매 실행마다 읽어오며, 조회 실패 시 DEFAULT_SETTINGS로 대체
- 같은 날·종목·이벤트는 1회만 알리고, 낙폭이 escalation_step_pct 이상 더 깊어질 때만 재알림
"""

import asyncio
import html
import logging
from datetime import date, datetime, timedelta

import FinanceDataReader as fdr
import requests
from telegram.ext import Application

from core.fetcher.crypto import is_crypto_ticker
from core.fetcher.us_stock import is_us_stock_ticker
from database.repository import AssetRepository

logger = logging.getLogger(__name__)

# DB(detector_settings) 조회 실패 시 사용할 기본 기준값 (단위: %)
DEFAULT_SETTINGS: dict[str, float] = {
    "premarket_gap_pct": 2.0,
    "kr_prev_close_drop_pct": 3.0,
    "kr_high_drawdown_pct": 2.5,
    "us_prev_close_drop_pct": 3.0,
    "us_high_drawdown_pct": 2.5,
    "crypto_prev_close_drop_pct": 5.0,
    "crypto_high_drawdown_pct": 3.5,
    "escalation_step_pct": 2.0,
}

EVENT_PREV_CLOSE_DROP = "PREV_CLOSE_DROP"
EVENT_HIGH_DRAWDOWN = "HIGH_DRAWDOWN"
EVENT_PREMARKET_GAP = "PREMARKET_GAP"

# DB 장애 시에도 같은 프로세스 안에서는 중복 발송을 막기 위한 보조 캐시: {(일자, 종목, 이벤트): 마지막 알림 크기}
_LOCAL_ALERT_CACHE: dict[tuple, float] = {}


def _load_settings(repo: AssetRepository) -> dict[str, float]:
    settings = dict(DEFAULT_SETTINGS)
    settings.update(repo.get_detector_settings())
    return settings


def _should_alert(
    repo: AssetRepository, alert_date, code: str, event: str, severity: float, settings
) -> bool:
    """
    severity(양수, 하락/변동 크기 %)가 처음 기준을 넘었거나,
    마지막 알림보다 escalation_step_pct 이상 더 커졌을 때만 True
    """
    key = (str(alert_date), code, event)
    last_db = repo.get_last_anomaly_alert_pct(alert_date, code, event)
    candidates = [abs(v) for v in (last_db, _LOCAL_ALERT_CACHE.get(key)) if v is not None]
    if not candidates:
        return True
    return severity >= max(candidates) + settings["escalation_step_pct"]


def _record_alerts(repo: AssetRepository, records: list[tuple]):
    """발송 성공 후 알림 기록 저장 (records: [(alert_date, code, event, change_pct), ...])"""
    for alert_date, code, event, change_pct in records:
        _LOCAL_ALERT_CACHE[(str(alert_date), code, event)] = abs(change_pct)
        repo.save_anomaly_alert(alert_date, code, event, change_pct)


def _holding_names(holdings: list[dict], predicate) -> dict[str, str]:
    """보유 종목 중 조건에 맞는 {종목코드: 종목명} (계좌별 중복 제거)"""
    names = {}
    for h in holdings:
        code = h.get("ticker_code")
        if code and predicate(code) and code not in names:
            names[code] = h.get("ticker_name") or code
    return names


def _is_kr_stock_ticker(code) -> bool:
    return str(code).isdigit() and len(str(code)) == 6


def _fetch_daily_quote(code: str, require_today: bool) -> dict | None:
    """
    FDR 일봉에서 {session_date, prev_close, high, price} 추출
    - require_today=True: 마지막 행이 오늘(KST)이 아니면 None (국내 휴장일/장 시작 전 오탐 방지)
    - require_today=False: 마지막 행이 최근 5일 이내 세션이면 사용 (미국 세션은 KST와 날짜가 다름)
    """
    start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    df = fdr.DataReader(code, start_date)
    if df is None or len(df) < 2:
        return None

    session_date = df.index[-1].date()
    today = date.today()
    if require_today and session_date != today:
        return None
    if not require_today and (today - session_date).days > 5:
        return None

    prev_close = float(df["Close"].iloc[-2])
    high = float(df["High"].iloc[-1])
    price = float(df["Close"].iloc[-1])
    if not (prev_close > 0 and high > 0 and price > 0):
        return None
    return {"session_date": session_date, "prev_close": prev_close, "high": high, "price": price}


def _fetch_crypto_quotes(codes: list[str]) -> dict[str, dict]:
    """Upbit 현재가 API로 {마켓: {session_date, prev_close, high, price}} 일괄 조회 (일봉 기준 09:00 KST 리셋)"""
    url = f"https://api.upbit.com/v1/ticker?markets={','.join(codes)}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()

    session_date = (datetime.now() - timedelta(hours=9)).date()
    quotes = {}
    for item in response.json():
        market = item.get("market")
        prev_close = float(item.get("prev_closing_price") or 0)
        high = float(item.get("high_price") or 0)
        price = float(item.get("trade_price") or 0)
        if market and prev_close > 0 and high > 0 and price > 0:
            quotes[market] = {
                "session_date": session_date,
                "prev_close": prev_close,
                "high": high,
                "price": price,
            }
    return quotes


def _evaluate_quote(repo, code: str, name: str, quote: dict, prefix: str, settings, fmt) -> tuple[list, list]:
    """전일 대비 하락 / 당일 고점 대비 낙폭 판정 → (메시지 라인, 발송 후 저장할 기록)"""
    lines, records = [], []
    alert_date = quote["session_date"]
    label = f"<b>{html.escape(str(name))}</b>"
    if name != code:
        label += f" ({html.escape(str(code))})"

    prev_pct = (quote["price"] - quote["prev_close"]) / quote["prev_close"] * 100
    if -prev_pct >= settings[f"{prefix}_prev_close_drop_pct"] and _should_alert(
        repo, alert_date, code, EVENT_PREV_CLOSE_DROP, -prev_pct, settings
    ):
        lines.append(f"• {label}: 전일 대비 <b>{prev_pct:.2f}%</b> (현재가 {fmt(quote['price'])})")
        records.append((alert_date, code, EVENT_PREV_CLOSE_DROP, prev_pct))

    dd_pct = (quote["price"] - quote["high"]) / quote["high"] * 100
    if -dd_pct >= settings[f"{prefix}_high_drawdown_pct"] and _should_alert(
        repo, alert_date, code, EVENT_HIGH_DRAWDOWN, -dd_pct, settings
    ):
        lines.append(
            f"• {label}: 당일 고가({fmt(quote['high'])}) 대비 <b>{dd_pct:.2f}%</b> (현재가 {fmt(quote['price'])})"
        )
        records.append((alert_date, code, EVENT_HIGH_DRAWDOWN, dd_pct))

    return lines, records


def _fmt_krw(value: float) -> str:
    return f"{value:,.0f}원"


def _fmt_usd(value: float) -> str:
    return f"${value:,.2f}"


def _scan_stocks(predicate, prefix: str, require_today: bool, fmt) -> tuple[list, list]:
    """보유 주식 시세 조회 및 판정 (동기 함수, asyncio.to_thread로 실행)"""
    repo = AssetRepository()
    settings = _load_settings(repo)
    names = _holding_names(repo.get_current_holdings(), predicate)

    lines, records = [], []
    for code, name in names.items():
        try:
            quote = _fetch_daily_quote(code, require_today)
        except Exception as e:
            logger.warning(f"이상징후 시세 조회 실패 [{code}]: {e}")
            continue
        if quote:
            item_lines, item_records = _evaluate_quote(repo, code, name, quote, prefix, settings, fmt)
            lines += item_lines
            records += item_records
    return lines, records


def _scan_crypto() -> tuple[list, list]:
    """보유 가상자산 시세 일괄 조회 및 판정 (동기 함수, asyncio.to_thread로 실행)"""
    repo = AssetRepository()
    settings = _load_settings(repo)
    names = _holding_names(repo.get_current_holdings(), is_crypto_ticker)
    if not names:
        return [], []

    quotes = _fetch_crypto_quotes(list(names))
    lines, records = [], []
    for code, name in names.items():
        quote = quotes.get(code)
        if quote:
            item_lines, item_records = _evaluate_quote(repo, code, name, quote, "crypto", settings, _fmt_krw)
            lines += item_lines
            records += item_records
    return lines, records


async def _send_and_record(application: Application, chat_id: str, title: str, lines: list, records: list):
    if not lines:
        return
    msg = f"🚨 <b>{title}</b>\n\n" + "\n".join(lines)
    await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
    await asyncio.to_thread(_record_alerts, AssetRepository(), records)
    logger.info(f"{title} 발송 완료: {len(lines)}건")


def _scan_premarket() -> tuple[str | None, list]:
    """간밤 S&P 500 전일 종가 대비 등락률 판정 (동기 함수)"""
    repo = AssetRepository()
    settings = _load_settings(repo)
    quote = _fetch_daily_quote("US500", require_today=False)
    if not quote:
        return None, []

    pct = (quote["price"] - quote["prev_close"]) / quote["prev_close"] * 100
    alert_date = date.today()
    if abs(pct) < settings["premarket_gap_pct"] or not _should_alert(
        repo, alert_date, "US500", EVENT_PREMARKET_GAP, abs(pct), settings
    ):
        return None, []

    direction = "🔺 갭상승" if pct > 0 else "🔷 갭하락"
    msg = (
        f"⚠️ <b>[장전 이상징후 경보] 오늘 증시 {direction} 출발 유력</b>\n\n"
        f"• 간밤({quote['session_date']:%m/%d}) 미국 S&P 500: <b>{pct:+.2f}%</b>\n"
        "• 국내 개장(09:00) 시 보유 종목 변동성에 유의하세요."
    )
    return msg, [(alert_date, "US500", EVENT_PREMARKET_GAP, pct)]


async def check_premarket_anomaly(application: Application, chat_id: str):
    """
    [장전/프리마켓 감시 - 평일 08:30~08:55]
    간밤 미국 S&P 500 전일 대비 등락률이 기준(premarket_gap_pct) 이상이면 하루 1회 발송
    """
    try:
        msg, records = await asyncio.to_thread(_scan_premarket)
        if msg:
            await application.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            await asyncio.to_thread(_record_alerts, AssetRepository(), records)
            logger.info(f"장전 프리마켓 갭 경보 발송 완료: {records[0][3]:+.2f}%")
    except Exception as e:
        logger.error(f"check_premarket_anomaly 오류: {e}")


async def check_intraday_anomaly(application: Application, chat_id: str):
    """
    [국내 정규장 감시 - 평일 09:05~15:25]
    보유 국내 주식/ETF의 전일 대비 급락 및 당일 고점 대비 낙폭 감시
    (가상자산은 check_crypto_anomaly에서 24시간 감시)
    """
    try:
        lines, records = await asyncio.to_thread(_scan_stocks, _is_kr_stock_ticker, "kr", True, _fmt_krw)
        await _send_and_record(application, chat_id, "[국내 장중 이상징후 경보]", lines, records)
    except Exception as e:
        logger.error(f"check_intraday_anomaly 오류: {e}")


async def check_us_anomaly(application: Application, chat_id: str):
    """
    [미국 정규장 감시 - KST 22:00~06:59]
    보유 미국 주식의 전일 대비 급락 및 당일 고점 대비 낙폭 감시 (세션 일자 기준 중복 방지)
    """
    try:
        lines, records = await asyncio.to_thread(_scan_stocks, is_us_stock_ticker, "us", False, _fmt_usd)
        await _send_and_record(application, chat_id, "[미국 장중 이상징후 경보]", lines, records)
    except Exception as e:
        logger.error(f"check_us_anomaly 오류: {e}")


async def check_crypto_anomaly(application: Application, chat_id: str):
    """
    [가상자산 24시간 감시]
    보유 코인의 전일(09:00 KST 기준) 대비 급락 및 당일 고점 대비 낙폭 감시
    """
    try:
        lines, records = await asyncio.to_thread(_scan_crypto)
        await _send_and_record(application, chat_id, "[가상자산 이상징후 경보]", lines, records)
    except Exception as e:
        logger.error(f"check_crypto_anomaly 오류: {e}")


async def check_aftermarket_anomaly(application: Application, chat_id: str):
    """
    [시간외 단일가 감시 - 평일 16:15~18:00]
    정규장 종가 대비 시간외 시세 급변(±3.0% 이상) 종목 감시 (스켈레톤)
    """
    try:
        pass
    except Exception as e:
        logger.error(f"check_aftermarket_anomaly 오류: {e}")
