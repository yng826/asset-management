"""
core/fetcher/__init__.py
- 자산군별 시세 수집 모듈 re-export 및 공통 인터페이스
"""

from core.fetcher.crypto import (
    collect_crypto_prices,
    fetch_crypto_price,
    is_crypto_ticker,
)
from core.fetcher.fund import (
    collect_fund_prices,
    fetch_fund_nav,
    get_fund_nav_overrides,
    is_fund_ticker,
    set_fund_nav_overrides,
)
from core.fetcher.fx import (
    collect_fx_rate,
    fetch_and_save_benchmarks,
    fetch_usd_krw_rate,
    is_cash_asset,
)
from core.fetcher.kr_stock import (
    collect_kr_prices,
    fetch_kr_stock_close,
    is_kr_stock_ticker,
    upsert_daily_price,
)
from core.fetcher.us_stock import (
    collect_us_prices,
    fetch_us_stock_close,
    is_us_stock_ticker,
)
from database.repository import AssetRepository


def collect_holdings_prices(verbose: bool = True) -> dict:
    """
    거래 원장에서 현재 잔고가 남아있는(quantity > 0) 종목을 조회한 뒤,
    모든 자산군(국내 주식, 해외 주식, 펀드, 코인, 환율)에 대해 시세를 수집하여
    daily_prices 테이블에 UPSERT 수행.
    """
    repo = AssetRepository()
    holdings = repo.get_current_holdings()

    summary = {
        "total_holdings": len(holdings),
        "kr_targets": 0,
        "us_targets": 0,
        "fund_targets": 0,
        "crypto_targets": 0,
        "fx_target": 1,
        "fetched": 0,
        "saved": 0,
        "skipped": 0,
        "failed": 0,
        "results": [],
    }

    # 각 자산군별 개별 수집 실행
    kr_summary = collect_kr_prices(verbose=verbose)
    us_summary = collect_us_prices(verbose=verbose)
    fund_summary = collect_fund_prices(verbose=verbose)
    crypto_summary = collect_crypto_prices(verbose=verbose)
    fx_summary = collect_fx_rate(verbose=verbose)

    summary["kr_targets"] = kr_summary["kr_targets"]
    summary["us_targets"] = us_summary["us_targets"]
    summary["fund_targets"] = fund_summary["fund_targets"]
    summary["crypto_targets"] = crypto_summary["crypto_targets"]

    for sub in [kr_summary, us_summary, fund_summary, crypto_summary, fx_summary]:
        summary["fetched"] += sub["fetched"]
        summary["saved"] += sub["saved"]
        summary["failed"] += sub["failed"]
        summary["results"].extend(sub["results"])

    # 스킵 카운트 산정 (전체 보유 종목 중 위 자산군에 해당하지 않는 현금/예금 등)
    processed_count = (
        summary["kr_targets"] + summary["us_targets"] + summary["fund_targets"] + summary["crypto_targets"]
    )
    summary["skipped"] = max(0, summary["total_holdings"] - processed_count)

    if verbose:
        print("\n=== 일일 종가/환율 수집 요약 ===")
        print(f"  전체 보유 종목: {summary['total_holdings']}")
        print(f"  국내 주식/ETF 대상: {summary['kr_targets']}")
        print(f"  해외주식 대상: {summary['us_targets']}")
        print(f"  펀드 대상: {summary['fund_targets']}")
        print(f"  가상자산 대상: {summary['crypto_targets']}")
        print(f"  환율 대상: {summary['fx_target']}")
        print(f"  수집 성공: {summary['fetched']}")
        print(f"  DB UPSERT 성공: {summary['saved']}")
        print(f"  스킵(현금/예금): {summary['skipped']}")
        print(f"  실패: {summary['failed']}")

    return summary


__all__ = [
    "collect_holdings_prices",
    "collect_kr_prices",
    "collect_us_prices",
    "collect_fund_prices",
    "collect_crypto_prices",
    "collect_fx_rate",
    "fetch_and_save_benchmarks",
    "is_kr_stock_ticker",
    "is_us_stock_ticker",
    "is_fund_ticker",
    "is_crypto_ticker",
    "is_cash_asset",
    "fetch_kr_stock_close",
    "fetch_us_stock_close",
    "fetch_usd_krw_rate",
    "fetch_fund_nav",
    "fetch_crypto_price",
    "set_fund_nav_overrides",
    "get_fund_nav_overrides",
    "upsert_daily_price",
]
