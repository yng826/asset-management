"""
core/health.py
- 봇 운영 상태 점검: 배치 실행 여부, 결산 스냅샷, 자산군별 시세 최신성, 최근 에러 로그.
- /health 명령어로 조회하고, 매일 16:30 점검에서 문제가 있을 때만 텔레그램 알림 (같은 항목은 하루 1회).

시세 최신성은 달력 대신 지수 최신 거래일과 비교해 휴장일(한글날·추석 등)을 오탐하지 않는다.
    국내 주식·ETF : 보유 종목 최신 시세일 ≥ KOSPI(KS11) 최신일
    미국 주식     : 보유 종목 최신 시세일 ≥ S&P500(US500) 최신일
    펀드 NAV      : ≥ KOSPI 직전 거래일 (기준가 T+1 공시)
    USD/KRW       : ≥ KOSPI 직전 거래일
    가상자산      : ≥ 어제 (09:01 전일 종가 수집, 09:05 이전이면 그제)
    지수 자체     : 최신일이 STALE_INDEX_DAYS 일보다 오래되면 경고 (지수 수집 중단)
"""

import html
import logging
import os
import re
from datetime import datetime, timedelta

from database.connection import fetch_all
from database.repository import AssetRepository

OK, WARN, FAIL = "OK", "WARN", "FAIL"
ICONS = {OK: "✅", WARN: "⚠️", FAIL: "❌"}
ALERT_EVENT = "HEALTH"

STALE_INDEX_DAYS = 6  # 긴 연휴(추석 등)를 감안한 지수 최신일 허용 범위
LOG_FILE = "logs/app.log"
LOG_TAIL_BYTES = 2_000_000
_LOG_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - (\S+) - (ERROR|CRITICAL) - (.*)$")
_ANY_LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ - ")
_JOB_FAIL = re.compile(r'^Job "(\w+) \(trigger:.*raised an exception$')


def _d(value) -> str:
    return str(value)[:10] if value else "-"


def _check(key: str, label: str, status: str, detail: str) -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail}


def _expected_closing_date(now: datetime) -> str:
    """16:00 결산(매일)이 끝났어야 하는 가장 최근 일자 (16:05 이전이면 어제)."""
    day = now if now.hour * 60 + now.minute >= 16 * 60 + 5 else now - timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def _expected_morning_date(now: datetime) -> str:
    """평일 08:55 오전 브리핑이 끝났어야 하는 가장 최근 평일."""
    day = now if now.hour * 60 + now.minute >= 9 * 60 else now - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.strftime("%Y-%m-%d")


def _check_batches(now: datetime) -> list[dict]:
    checks = []
    for key, batch, label, expected in [
        ("closing", "closing_1600", "일일 결산 (16:00)", _expected_closing_date(now)),
        ("morning", "morning_0845", "오전 브리핑 (평일 08:55)", _expected_morning_date(now)),
    ]:
        rows = fetch_all(
            """
            SELECT execution_date, execution_time, status, message FROM batch_execution_logs
            WHERE batch_name = ? ORDER BY log_id DESC LIMIT 1
            """,
            (batch,),
            strict=True,
        )
        last_date, last_time, last_status, last_message = rows[0] if rows else (None, None, None, None)
        detail = f"마지막 실행 {str(last_time)[5:16]}" if last_time else "실행 기록 없음"
        status = OK if last_date and _d(last_date) >= expected else FAIL
        if status == FAIL:
            detail += f" (기대 {expected[5:]})"
        elif last_status in ("FAILED", "WARNING"):
            # 실행은 됐지만 수집 오류·시세 지연으로 기록된 경우 (core.scheduler._record_batch 판정)
            status = FAIL if last_status == "FAILED" else WARN
            note = str(last_message or "").split(" | ", 1)[-1]
            detail += f" · {last_status}: {note[:80]}"
        checks.append(_check(key, label, status, detail))

    rows = fetch_all("SELECT MAX(snapshot_date) FROM daily_snapshots", strict=True)
    latest = _d(rows[0][0]) if rows else None
    expected = _expected_closing_date(now)
    checks.append(
        _check(
            "snapshot",
            "결산 스냅샷",
            OK if latest and latest >= expected else FAIL,
            f"최신 {latest[5:] if latest else '-'}"
            + ("" if latest and latest >= expected else f" (기대 {expected[5:]})"),
        )
    )
    return checks


def _latest_dates(ticker: str, n: int = 2) -> list[str]:
    rows = fetch_all(
        "SELECT price_date FROM daily_prices WHERE ticker_code = ? ORDER BY price_date DESC LIMIT ?",
        (ticker, n),
        strict=True,
    )
    return [_d(r[0]) for r in rows]


def _held_ticker_dates() -> dict[str, list[tuple[str, str]]]:
    """최신 결산 스냅샷 보유 종목의 자산군별 [(종목코드, 최신 시세일)]."""
    rows = fetch_all(
        """
        SELECT h.ticker_code, (SELECT MAX(p.price_date) FROM daily_prices p WHERE p.ticker_code = h.ticker_code)
        FROM (
            SELECT DISTINCT ticker_code FROM daily_holding_snapshots
            WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM daily_holding_snapshots) AND quantity > 0
        ) h
        """,
        strict=True,
    )
    groups: dict[str, list[tuple[str, str]]] = {"kr": [], "us": [], "crypto": [], "fund": []}
    for code, last in rows:
        code = str(code)
        if code.startswith("KRW-"):
            groups["crypto"].append((code, _d(last)))
        elif re.match(r"^(KR5|K5)", code):
            groups["fund"].append((code, _d(last)))
        elif re.match(r"^[0-9][0-9A-Z]{5}$", code):
            groups["kr"].append((code, _d(last)))
        elif re.match(r"^[A-Z]{1,5}$", code):
            groups["us"].append((code, _d(last)))
    return groups


def _check_prices(now: datetime) -> list[dict]:
    checks = []
    today = now.strftime("%Y-%m-%d")
    kospi = _latest_dates("KS11")
    sp500 = _latest_dates("US500")

    # 지수 수집 자체가 멈췄는지
    for key, label, dates in [("idx_kr", "KOSPI 지수", kospi), ("idx_us", "S&P500 지수", sp500)]:
        latest = dates[0] if dates else None
        stale = not latest or (now - datetime.strptime(latest, "%Y-%m-%d")).days > STALE_INDEX_DAYS
        checks.append(_check(key, label, WARN if stale else OK, f"최신 {latest[5:] if latest else '-'}"))

    held = _held_ticker_dates()
    crypto_cutoff = (now - timedelta(days=1 if now.hour * 60 + now.minute >= 9 * 60 + 5 else 2)).strftime(
        "%Y-%m-%d"
    )
    rules = [
        ("kr", "국내 주식·ETF 시세", kospi[0] if kospi else None),
        ("us", "미국 주식 시세", sp500[0] if sp500 else None),
        ("fund", "펀드 기준가", kospi[1] if len(kospi) > 1 else None),
        ("crypto", "가상자산 시세", crypto_cutoff),
    ]
    for key, label, ref in rules:
        items = held.get(key, [])
        if not items or not ref:
            continue
        stale = sorted((last, code) for code, last in items if last < ref)
        if stale:
            names = ", ".join(f"{code}({last[5:]})" for last, code in stale[:4])
            checks.append(_check(f"price_{key}", label, WARN, f"기준 {ref[5:]}보다 오래됨: {names}"))
        else:
            latest = min(last for _, last in items)
            checks.append(_check(f"price_{key}", label, OK, f"{len(items)}종목 최신 {latest[5:]}"))

    fx = _latest_dates("USD/KRW", 1)
    fx_ref = kospi[1] if len(kospi) > 1 else today
    fx_latest = fx[0] if fx else None
    checks.append(
        _check(
            "price_fx",
            "USD/KRW 환율",
            OK if fx_latest and fx_latest >= fx_ref else WARN,
            f"최신 {fx_latest[5:] if fx_latest else '-'}",
        )
    )
    return checks


# 배치별로 수집을 책임지는 시세 점검 항목 (_check_prices 의 key)
BATCH_PRICE_KEYS = {
    "morning_0845": ["price_us", "price_fund", "price_fx", "price_kr"],
    "closing_1600": ["price_kr", "price_crypto"],
}


def judge_batch(
    batch_name: str, error: Exception | None = None, now: datetime | None = None
) -> tuple[str, str]:
    """
    배치 직후 감사 로그용 상태 판정 → (status, 비고).
    - 수집 파이프라인 예외: FAILED
    - 배치가 책임지는 자산군 시세가 지수 최신 거래일보다 오래됨: WARNING (휴장일은 지수도 그대로라 SUCCESS)
    - 그 외 SUCCESS / 대상이 아닌 배치(probe 등)는 INFO
    """
    if error is not None:
        return "FAILED", f"수집 오류: {error}"
    keys = BATCH_PRICE_KEYS.get(batch_name)
    if not keys:
        return "INFO", ""
    try:
        checks = {c["key"]: c for c in _check_prices(now or datetime.now())}
    except Exception as e:
        return "WARNING", f"시세 점검 실패: {e}"
    stale = [checks[k] for k in keys if k in checks and checks[k]["status"] != OK]
    if stale:
        return "WARNING", "지연: " + "; ".join(f"{c['label']} {c['detail']}" for c in stale)
    return "SUCCESS", ""


def _check_error_log(now: datetime, hours: int = 24) -> dict:
    """최근 hours 시간 ERROR/CRITICAL 로그 건수와 마지막 메시지 (로그 파일 끝 LOG_TAIL_BYTES 만 읽음)."""
    if not os.path.exists(LOG_FILE):
        return _check("errors", "에러 로그 (24h)", OK, "로그 파일 없음")
    # 회전 직후에는 최근 기록이 app.log.1 에 있으므로 함께 읽음 (오래된 파일 먼저)
    lines = []
    for path in [f"{LOG_FILE}.1", LOG_FILE]:
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - LOG_TAIL_BYTES))
            lines += f.read().decode("utf-8", errors="replace").splitlines()

    since = (now - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    errors = []  # [시각, 메시지, 바로 뒤따르는 traceback 줄들]
    current = None  # traceback 을 이어 붙일 직전 에러 항목
    for line in lines:
        m = _LOG_LINE.match(line)
        if m:
            current = [m.group(1), m.group(4), []] if m.group(1) >= since else None
            if current:
                errors.append(current)
        elif _ANY_LOG_LINE.match(line):
            current = None
        elif current is not None:
            current[2].append(line)

    # 봇 재시작(watchmedo·배포) 시 실행 중이던 작업이 끊긴 CancelledError 는 장애가 아니므로 제외
    errors = [(t, msg) for t, msg, tb in errors if not any("CancelledError" in x for x in tb)]
    # 스케줄러 작업 실패는 작업 이름만 표시
    errors = [(t, _JOB_FAIL.sub(r"\1 작업 실패", msg)) for t, msg in errors]
    if not errors:
        return _check("errors", "에러 로그 (24h)", OK, "없음")
    last_time, last_msg = errors[-1]
    return _check(
        "errors", "에러 로그 (24h)", WARN, f"{len(errors)}건, 마지막 {last_time[5:16]} {last_msg[:80]}"
    )


def collect_health(now: datetime | None = None) -> list[dict]:
    """전체 점검 결과 [{"key", "label", "status", "detail"}]. 개별 점검 실패는 FAIL 항목으로 담는다."""
    now = now or datetime.now()
    checks = []
    for name, func in [("batch", _check_batches), ("price", _check_prices)]:
        try:
            checks += func(now)
        except Exception as e:
            logging.error(f"❌ 상태 점검 실패 [{name}]: {e}", exc_info=True)
            checks.append(_check(f"{name}_check", f"{name} 점검", FAIL, f"점검 중 오류: {e}"))
    checks.append(_check_error_log(now))
    return checks


def format_health(checks: list[dict], title: str = "🩺 <b>봇 상태 점검</b>") -> str:
    problems = [c for c in checks if c["status"] != OK]
    summary = "모두 정상" if not problems else f"문제 {len(problems)}건"
    lines = [f"{title} ({datetime.now().strftime('%m-%d %H:%M')}) — {summary}", ""]
    for c in checks:
        lines.append(f"{ICONS[c['status']]} {html.escape(c['label'])}: {html.escape(c['detail'])}")
    return "\n".join(lines)


def _alerted_today(key: str) -> bool:
    return AssetRepository().has_recent_anomaly_alert(f"HEALTH:{key}", ALERT_EVENT, days=1)


def _record_alert(key: str) -> None:
    AssetRepository().record_anomaly_alert(f"HEALTH:{key}", ALERT_EVENT)


async def check_health_and_alert(application, chat_id: str) -> None:
    """[매일 16:30] 상태 점검 후 문제 항목만 알림 (같은 항목은 하루 1회)."""
    try:
        checks = collect_health()
        new_problems = [c for c in checks if c["status"] != OK and not _alerted_today(c["key"])]
    except Exception as e:
        logging.error(f"❌ 정기 상태 점검 실패: {e}", exc_info=True)
        return
    if not new_problems:
        logging.info("🩺 정기 상태 점검: 이상 없음")
        return

    lines = [f"🩺 <b>상태 점검 경고</b> ({datetime.now().strftime('%m-%d %H:%M')})", ""]
    lines += [
        f"{ICONS[c['status']]} {html.escape(c['label'])}: {html.escape(c['detail'])}" for c in new_problems
    ]
    lines.append("")
    lines.append("<i>/health 로 전체 현황 확인</i>")
    await application.bot.send_message(chat_id=chat_id, text="\n".join(lines), parse_mode="HTML")
    for c in new_problems:
        _record_alert(c["key"])
    logging.info(f"🩺 상태 점검 경고 발송: {[c['key'] for c in new_problems]}")
