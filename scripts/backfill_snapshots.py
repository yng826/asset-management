import sys
from datetime import datetime, timedelta

sys.path.append(".")

from core.calculator import (
    enrich_holdings_with_prices,
    get_fx_rate_as_of_date,
    get_prices_map_as_of_date,
)
from database.repository import AssetRepository


def backfill_snapshots(start_date: str, end_date: str):
    repo = AssetRepository()
    curr = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    print(f"🔄 {start_date} ~ {end_date} 스냅샷 백필 시작...")

    while curr <= end:
        d_str = curr.strftime("%Y-%m-%d")
        holdings = repo.get_holdings_as_of_date(d_str)
        if not holdings:
            curr += timedelta(days=1)
            continue

        prices = get_prices_map_as_of_date(d_str)
        fx = get_fx_rate_as_of_date(d_str)

        enriched = enrich_holdings_with_prices(holdings, prices, fx)

        total_eval = sum(it["valuation_amount"] for it in enriched)
        total_invested = sum(it["buy_amount"] for it in enriched)
        cash = sum(it["valuation_amount"] for it in enriched if "CASH" in it.get("ticker_code", ""))

        repo.save_snapshot(
            d_str,
            {"total_eval_amount": total_eval, "total_invested_amount": total_invested, "cash_amount": cash},
        )
        print(f"✅ {d_str} 스냅샷 생성 완료: {total_eval:,.0f}원")
        curr += timedelta(days=1)


if __name__ == "__main__":
    # 최근 30일치만 실행
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    backfill_snapshots(start, datetime.now().strftime("%Y-%m-%d"))
