"""
core/valuator/crypto.py
- 가상자산(코인) 평가 로직 (스텁).

현재 자산 보유 내역에 코인이 없어 미구현 상태.
향후 Upbit/Bithumb API 등 연동 시 아래 인터페이스를 채워 사용:

  1) is_crypto_ticker(ticker_code) -> bool
     - 예: BTC/KRW (업비트), ETH/KRW, XRP/KRW 등

  2) fetch_crypto_price(ticker_code) -> {price_date, close_price (KRW)}
     - pyupbit 등 라이브러리 또는 직접 REST API

  3) valuation(asset, price_map, fx_rate) -> dict | None
     - 매칭 시: current_price = KRW 시세, valuation_amount = qty × current_price
     - price_source = "crypto"
"""
# 향후 확장 시 위 helper 들을 구현하고 valuation() 에서 위임.


def valuation(asset: dict, price_map: dict, fx_rate: dict | None) -> dict | None:
    """valuator 공통 인터페이스 — 스텁. 현재는 매칭하지 않음."""
    return None
