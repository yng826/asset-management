"""
core/price_fetcher.py
- core/fetcher/ 패키지로 모듈화된 시세 수집 로직을 위임하는 하위 호환 래퍼
"""

from core.fetcher import (
    collect_crypto_prices,
    collect_fund_prices,
    collect_fx_rate,
    collect_holdings_prices,
    collect_kr_prices,
    collect_us_prices,
    fetch_and_save_benchmarks,
    fetch_crypto_price,
    fetch_fund_nav,
    fetch_kr_stock_close,
    fetch_us_stock_close,
    fetch_usd_krw_rate,
    get_fund_nav_overrides,
    is_cash_asset,
    is_crypto_ticker,
    is_fund_ticker,
    is_kr_stock_ticker,
    is_us_stock_ticker,
    set_fund_nav_overrides,
    upsert_daily_price,
)
from database.connection import get_connection

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


if __name__ == "__main__":
    print("🚀 일일 종가/환율 수집 파이프라인 시작...")
    result = collect_holdings_prices(verbose=True)

    # 수집 직후 daily_prices 테이블 간단 조회
    conn = get_connection()
    if conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT price_date, ticker_code, close_price "
            "FROM daily_prices ORDER BY price_date DESC, ticker_code ASC"
        )
        rows = cur.fetchall()
        print(f"\n📦 daily_prices 현재 row 수: {len(rows)}")
        for row in rows[:40]:
            code = row[1]
            mark = "💱" if code == "USD/KRW" else "📈"
            print(f"  - {row[0]} | {mark} {code:10s} | {float(row[2]):,.4f}")
        cur.close()
        conn.close()
