"""
core/valuator
- 자산군별 평가 로직을 분리한 valuator 모듈 패키지.
- calculator 의 enrich_holdings_with_prices() 가 자산군별로 적절한 valuator
  를 위임 호출하는 구조.

각 valuator 인터페이스 (공통):
    def valuation(asset: dict, price_map: dict, fx_rate: dict | None) -> dict | None:
        Args:
            asset: holdings row dict
                   (ticker_name, ticker_code, quantity, avg_price, ...)
            price_map: {ticker_code: {price_date, close_price}}
            fx_rate:   {price_date, rate} (USD/KRW 등)
        Returns:
            매칭 성공 시 enriched dict:
                {
                  ...asset 원본 필드,
                  "current_price": float,
                  "price_date": date,
                  "price_source": "deposit" | "us_stock" | "fund_nav" | "market" | "fallback",
                  "buy_amount": float,
                  "valuation_amount": float,
                  "profit": float,
                  "pnl_rate": float,
                  "_<asset_class>": {... 자산군별 메타 dict ...},
                }
            매칭 실패 시 None (calculator 가 다음 valuator 또는 fallback 시도)
"""

from core.valuator import deposit, fund, stock  # crypto 는 향후 확장

__all__ = ["deposit", "fund", "stock", "crypto"]
