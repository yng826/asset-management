"""collector - 시세 수집 1회 실행 진입점 (k3s CronJob 이 Job 마다 하나씩 실행)

봇 APScheduler 의 수집 작업과 같은 함수(core.collection)를 호출하고, 감사 로그도 같은 이름으로 기록한다.
텔레그램 발송은 하지 않는다 (리포트는 봇이 DB 를 읽어 발송).

섀도 모드 (PRICE_TABLE=daily_prices_k3s, 봇과 병행 운영 기간):
  시세만 daily_prices_k3s 에 저장하고 스냅샷·자가 치유·감사 로그·종목 마스터는 건드리지 않는다.
  python -m collector compare 로 기존 daily_prices 와 종가를 비교.

사용법:
  python -m collector --list
  python -m collector morning              # 평일 오전 수집 (09:02)
  python -m collector probe probe_0830     # 장전 사전 수집 점검
  python -m collector indices              # 국가·해외 지수 최근 7일 (python -m collector indices 1100 = 약 3년 백필)
  python -m collector compare 7            # 최근 7일 daily_prices ↔ daily_prices_k3s 비교 (읽기 전용)
  python -m collector compare 7 daily_prices_broker   # 증권사 시세 섀도와 비교 (국내/미국 구분, 차이율)
  PRICE_TABLE=daily_prices_broker python -m collector broker-prices   # 증권사 종가 수집 (종목별 FDR 폴백)
  ENV_FILE=.env.dev python -m collector closing   # 개발 DB

종료 코드: 0 성공 / 1 수집 오류 (Job 실패로 표시되어 kubectl get jobs 에서 확인)
"""

import argparse
import logging
import sys

from config.settings import (  # env 파일 로드 (k3s 는 Secret·ConfigMap 환경변수 우선)
    ENV_FILE_PATH,
    PRICE_TABLE,
    SHADOW_MODE,
)

logger = logging.getLogger("collector")


def _morning(_: list[str]) -> Exception | None:
    from core.collection import check_and_auto_heal_missing_snapshots, collect_morning, record_batch
    from database.repository import AssetRepository

    if SHADOW_MODE:
        return collect_morning(snapshots=False)
    check_and_auto_heal_missing_snapshots()
    error = collect_morning()
    record_batch(AssetRepository(), "morning_0845", "오전 시세 수집 완료 (collector)", error)
    return error


def _closing(_: list[str]) -> Exception | None:
    from core.collection import check_and_auto_heal_missing_snapshots, collect_closing, record_batch
    from database.repository import AssetRepository

    if SHADOW_MODE:
        return collect_closing(snapshots=False)
    check_and_auto_heal_missing_snapshots()
    error = collect_closing()
    record_batch(AssetRepository(), "closing_1600", "일일 결산 시세 수집 완료 (collector)", error)
    return error


def _weekend(_: list[str]) -> Exception | None:
    from core.collection import check_and_auto_heal_missing_snapshots, collect_weekend, record_batch
    from database.repository import AssetRepository

    if SHADOW_MODE:
        return collect_weekend(snapshots=False)
    check_and_auto_heal_missing_snapshots()
    error = collect_weekend()
    record_batch(
        AssetRepository(),
        "closing_1600",
        "주말 결산 시세 수집 완료 (collector)",
        error,
        judge_as="closing_weekend",
    )
    return error


def _probe(args: list[str]) -> Exception | None:
    from core.collection import collect_probe
    from database.repository import AssetRepository

    probe_tag = args[0] if args else "probe_0830"
    error = collect_probe(probe_tag)
    if SHADOW_MODE:
        return error
    AssetRepository().record_batch_audit_log(probe_tag, message=f"{probe_tag} 조기 수집 가용성 점검")
    return error


def _crypto_close(_: list[str]) -> Exception | None:
    from core.fetcher import collect_crypto_prices

    summary = collect_crypto_prices(verbose=False)
    logger.info(f"가상자산 일봉 수집: 대상 {summary['crypto_targets']} 저장 {summary['saved']}")
    return RuntimeError(f"가상자산 {summary['failed']}건 수집 실패") if summary["failed"] else None


def _indices(args: list[str]) -> Exception | None:
    from core.fetcher.index import collect_index_prices

    days = int(args[0]) if args else 7
    summary = collect_index_prices(days)
    logger.info(
        f"국가·해외 지수 수집: 대상 {summary['targets']} 저장 {summary['saved_rows']}행 (최근 {days}일)"
    )
    return RuntimeError(f"지수 수집 실패: {', '.join(summary['failed'])}") if summary["failed"] else None


def _ticker_master(_: list[str]) -> Exception | None:
    from core.ticker_master import sync_ticker_master

    if SHADOW_MODE:
        logger.info("섀도 모드: 종목 마스터는 봇이 동기화하므로 생략")
        return None
    result = sync_ticker_master()
    return None if any(result.values()) else RuntimeError(f"종목 마스터 동기화 결과 없음: {result}")


def _print_snapshot_diff(holdings: list[dict]) -> None:
    """API 수량 ↔ 최신 daily_holding_snapshots 수량 비교 (단위 차이 확인용, 읽기 전용)."""
    from database.connection import fetch_all

    rows = fetch_all(
        "SELECT account_name, ticker_code, quantity FROM daily_holding_snapshots "
        "WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM daily_holding_snapshots) AND quantity > 0"
    )
    stored = {(r[0], r[1]): float(r[2]) for r in rows}
    for h in holdings:
        key = (h["account_name"], h["symbol"])
        if key not in stored:
            print(f"  수량비교 {key[0]} {key[1]}: 스냅샷에 없음 (API {h['quantity']:g})")
        elif abs(stored[key] - h["quantity"]) > 1e-6:
            ratio = h["quantity"] / stored[key]
            print(
                f"  수량비교 {key[0]} {key[1]}: 스냅샷 {stored[key]:g} / API {h['quantity']:g} (API/스냅샷 {ratio:g}배)"
            )
        else:
            print(f"  수량비교 {key[0]} {key[1]}: 일치 {h['quantity']:g}")


def _broker_check(args: list[str]) -> Exception | None:
    """증권사 API 수동 점검 (읽기 전용, DB 에는 토큰만 저장). 인자: <증권사> [종목...]"""
    from datetime import date, timedelta

    from core.broker import BrokerError, get_broker

    if not args:
        return RuntimeError("사용법: python -m collector broker-check toss|kis 005930 AAPL")
    try:
        broker = get_broker(args[0])
        for symbol in args[1:]:
            closes = broker.daily_closes(symbol, 5)
            print(
                f"  {symbol:<8} " + ", ".join(f"{c['price_date']:%m-%d} {c['close_price']:g}" for c in closes)
            )
        try:
            executions = broker.executions(date.today() - timedelta(days=7), date.today())
            print(f"  최근 7일 체결 {len(executions)}건")
        except NotImplementedError as e:
            print(f"  체결 조회 건너뜀: {e}")
        if broker.name == "kis":
            holdings = broker.holdings()
            print(
                f"  보유 종목 {len(holdings)}건: "
                + ", ".join(f"{h['account_name']} {h['symbol']}" for h in holdings)
            )
            for name, product in broker.skipped:
                print(f"  건너뜀(종목코드 없음) {name}: {product}")
            for name, reason in broker.failed:
                print(f"  조회 실패 {name}: {reason}")
            _print_snapshot_diff(holdings)
    except BrokerError as e:
        return e
    return None


def _broker_prices(_: list[str]) -> Exception | None:
    """증권사 시세 섀도: 국내·미국 종가만 daily_prices_broker 에 수집 (PRICE_TABLE=daily_prices_broker 필요)."""
    from core.fetcher.broker_prices import collect_broker_prices

    if PRICE_TABLE != "daily_prices_broker":
        return RuntimeError(f"PRICE_TABLE=daily_prices_broker 로 실행해야 합니다 (현재 {PRICE_TABLE})")
    s = collect_broker_prices()
    logger.info(
        f"증권사 시세 수집: 대상 {s['targets']} 증권사 {s['broker']} 폴백 {s['fallback']} 실패 {len(s['failed'])}"
        + (f" / 폴백 종목 {', '.join(s['fallback_codes'])}" if s["fallback_codes"] else "")
    )
    return RuntimeError(f"증권사 시세 수집 실패: {', '.join(s['failed'])}") if s["failed"] else None


def _compare(args: list[str]) -> Exception | None:
    """daily_prices(봇) ↔ 섀도 테이블 최근 N일 종가 비교. 읽기 전용. 인자: [일수] [테이블, 기본 daily_prices_k3s]"""
    from core.fetcher.kr_stock import is_kr_stock_ticker
    from core.fetcher.us_stock import is_us_stock_ticker
    from database.connection import fetch_all

    days = int(args[0]) if args else 7
    table = args[1] if len(args) > 1 else "daily_prices_k3s"
    if table not in {"daily_prices_k3s", "daily_prices_broker"}:
        return RuntimeError(f"비교 대상 테이블은 daily_prices_k3s / daily_prices_broker 만 가능: {table}")
    rows = fetch_all(
        f"""
        SELECT COALESCE(a.price_date, b.price_date) AS d, COALESCE(a.ticker_code, b.ticker_code) AS t,
               a.close_price AS bot, b.close_price AS other
        FROM daily_prices a
        LEFT JOIN {table} b ON a.price_date = b.price_date AND a.ticker_code = b.ticker_code
        WHERE a.price_date >= CURDATE() - INTERVAL ? DAY
        UNION
        SELECT b.price_date, b.ticker_code, a.close_price, b.close_price
        FROM {table} b
        LEFT JOIN daily_prices a ON a.price_date = b.price_date AND a.ticker_code = b.ticker_code
        WHERE b.price_date >= CURDATE() - INTERVAL ? DAY
        ORDER BY 1, 2
        """,
        (days, days),
        strict=True,
    )

    def kind(code: str) -> str:
        return "국내" if is_kr_stock_ticker(code) else "미국" if is_us_stock_ticker(code) else "기타"

    # IDX: 지수는 수집기만 모으므로 제외. 증권사 시세는 국내·미국 주식만 모으므로 그 외 자산군도 제외
    rows = [r for r in rows if not str(r[1]).startswith("IDX:")]
    if table == "daily_prices_broker":
        rows = [r for r in rows if kind(str(r[1])) != "기타"]
    diffs = [r for r in rows if r[2] is None or r[3] is None or abs(float(r[2]) - float(r[3])) > 1e-4]
    for d, t, bot, other in diffs:
        pct = f"{(float(other) - float(bot)) / float(bot) * 100:+.2f}%" if bot and other is not None else "-"
        print(f"  {d} {kind(str(t))} {t:<12} bot={bot} {table}={other} 차이 {pct}")
    for k in ("국내", "미국", "기타"):
        group = [r for r in rows if kind(str(r[1])) == k]
        if group:
            n = sum(1 for r in diffs if kind(str(r[1])) == k)
            print(f"  [{k}] {len(group)}건 중 불일치 {n}건")
    print(f"최근 {days}일 {table} 비교: {len(rows)}건 중 불일치 {len(diffs)}건 (None = 한쪽에만 있음)")
    return None


JOBS = {
    "morning": ("평일 오전 수집: 미국·환율·펀드·코인·국내·지수 + 최근 스냅샷 재계산", _morning),
    "closing": ("평일 결산 수집: 국내·코인·지수 + 당일 스냅샷", _closing),
    "weekend": ("주말 결산 수집: 전체 자산군 + 최근 스냅샷 재계산 + 당일 스냅샷", _weekend),
    "probe": ("장전 사전 수집 점검 (인자: probe_0830 등 감사 로그 태그)", _probe),
    "crypto-close": ("가상자산 전일 일봉 확정 종가 수집", _crypto_close),
    "indices": ("국가·해외 지수 일봉 수집 (인자: 최근 일수, 기본 7 / 백필은 큰 값)", _indices),
    "ticker-master": ("종목 마스터 동기화 (KRX·ETF·업비트·미국)", _ticker_master),
    "broker-check": ("증권사 API 수동 점검 (인자: toss [종목...]) 읽기 전용", _broker_check),
    "broker-prices": ("증권사 시세 섀도: 국내·미국 종가 → daily_prices_broker (FDR 폴백)", _broker_prices),
    "compare": (
        "섀도 비교: daily_prices ↔ 섀도 테이블 (인자: 일수=7 [daily_prices_k3s|daily_prices_broker])",
        _compare,
    ),
}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(prog="python -m collector", description="시세 수집 1회 실행")
    parser.add_argument("job", nargs="?", choices=list(JOBS), help="실행할 수집 작업")
    parser.add_argument("args", nargs="*", help="작업 인자 (probe 태그 등)")
    parser.add_argument("--list", action="store_true", help="작업 목록")
    opts = parser.parse_args()

    if opts.list or not opts.job:
        for key, (label, _) in JOBS.items():
            print(f"  {key:<14} {label}")
        return 0

    label, func = JOBS[opts.job]
    mode = f" [섀도: {PRICE_TABLE}]" if SHADOW_MODE else ""
    logger.info(f"▶️  {opts.job}: {label} ({ENV_FILE_PATH}){mode}")

    # 수집 함수는 DB 오류를 빈 결과로 삼키므로, 연결 불가는 먼저 확인해 Job 실패로 남김
    from database.connection import DBConnectionError, execute, fetch_all

    try:
        fetch_all("SELECT 1", strict=True)
    except DBConnectionError as e:
        logger.error(f"❌ DB 연결 실패 ({opts.job} 미실행): {e}")
        return 1
    if SHADOW_MODE:
        try:
            execute(f"CREATE TABLE IF NOT EXISTS {PRICE_TABLE} LIKE daily_prices")
        except Exception as e:
            logger.error(f"❌ {PRICE_TABLE} 테이블 준비 실패 (CREATE 권한 확인): {e}")
            return 1

    error = func(opts.args)
    if error is not None:
        logger.error(f"❌ {opts.job} 실패: {error}")
        return 1
    logger.info(f"✅ {opts.job} 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
