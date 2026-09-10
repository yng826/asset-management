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

        # 👇 [수정] 단가 및 평가액 계산 안전 보장
        holding_records = []
        for it in enriched:
            qty = float(it.get("quantity") or 0.0)
            u_price = float(it.get("current_price") or it.get("close_price") or 0.0)

            # 키가 없더라도 quantity * price 로 즉시 계산
            calc_eval = it.get("eval_amount") or it.get("valuation_amount") or (qty * u_price)
            calc_invest = it.get("invested_amount") or it.get("buy_amount") or 0.0

            holding_records.append(
                {
                    "snapshot_date": d_str,
                    "account_name": it["account_name"],
                    "ticker_code": it["ticker_code"],
                    "quantity": qty,
                    "close_price": u_price,
                    "eval_amount": float(calc_eval),
                    "invested_amount": float(calc_invest),
                }
            )

        repo.save_snapshot(
            d_str,
            {"total_eval_amount": total_eval, "total_invested_amount": total_invested, "cash_amount": cash},
        )
        repo.save_holding_snapshots(d_str, holding_records)
        print(f"✅ {d_str} 스냅샷 생성 완료: {total_eval:,.0f}원")
        curr += timedelta(days=1)


if __name__ == "__main__":
    # 실행 시 인자를 주면 해당 날짜로, 없으면 2026-05-01부터 어제까지 실행
    s_date = sys.argv[1] if len(sys.argv) > 1 else "2026-05-01"
    e_date = sys.argv[2] if len(sys.argv) > 2 else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    backfill_snapshots(s_date, e_date)
