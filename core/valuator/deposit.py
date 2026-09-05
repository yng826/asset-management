"""
core/valuator/deposit.py
- 정기예금/예금 자산 평가 로직 (일할 계산).
- ticker_name 이 "정기예금"/"예금" 이고, ticker_code 가 "이율|시작일|만기일" 형태일 때 매칭.

평가식:
    세전 이자 = 원금 × (연이율 / 100) × (경과일수 / 365)
    현재평가액 = 원금 + 세전 이자
    손익     = 평가액 - 원금
"""

from datetime import date, datetime

# 정기예금 식별
DEPOSIT_TICKER_NAMES = ("정기예금", "예금")
DEPOSIT_CODE_PARTS = 3  # "이율|시작일|만기일" 3-part


def is_deposit(ticker_name: str | None, ticker_code: str | None) -> bool:
    """정기예금/예금 자산 여부 판별.

    조건:
      1) ticker_name 이 DEPOSIT_TICKER_NAMES 중 하나
      2) ticker_code 가 '|'-separated 3-part ("rate|start|end")
    """
    if not ticker_name or not ticker_code:
        return False
    if ticker_name not in DEPOSIT_TICKER_NAMES:
        return False
    parts = str(ticker_code).split("|")
    return len(parts) == DEPOSIT_CODE_PARTS


def parse_deposit_metadata(ticker_code: str) -> dict | None:
    """정기예금 ticker_code "이율|시작일|만기일" 파싱.

    Returns:
        {
          "annual_rate": float,    # 연이율 (소수, 예: 4.42)
          "start_date": date,
          "end_date": date,        # 만기일
        } 또는 None (파싱 실패 시)
    """
    if not ticker_code:
        return None
    parts = str(ticker_code).split("|")
    if len(parts) != DEPOSIT_CODE_PARTS:
        return None
    try:
        annual_rate = float(parts[0])
        start_date = datetime.strptime(parts[1], "%Y-%m-%d").date()
        end_date = datetime.strptime(parts[2], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return {
        "annual_rate": annual_rate,
        "start_date": start_date,
        "end_date": end_date,
    }


def calculate_deposit_valuation(
    principal: float,
    annual_rate: float,
    start_date: date,
    end_date: date,
    today: date | None = None,
) -> dict:
    """일할 계산으로 정기예금 현재 평가액을 산출.

    Returns:
        {
          "principal", "elapsed_days", "interest_gross", "valuation_amount",
          "profit", "annual_rate", "start_date", "end_date", "as_of", "is_matured"
        }
    """
    if today is None:
        today = date.today()

    raw_elapsed = (today - start_date).days
    if raw_elapsed < 0:
        raw_elapsed = 0

    max_elapsed = (end_date - start_date).days
    if max_elapsed < 0:
        max_elapsed = 0
    elapsed_days = min(raw_elapsed, max_elapsed)
    is_matured = raw_elapsed >= max_elapsed and max_elapsed > 0

    # 세전 이자 = 원금 × (연이율/100) × (경과일수/365)
    interest_gross = principal * (annual_rate / 100.0) * (elapsed_days / 365.0)
    valuation_amount = principal + interest_gross

    return {
        "principal": principal,
        "elapsed_days": elapsed_days,
        "interest_gross": interest_gross,
        "valuation_amount": valuation_amount,
        "profit": interest_gross,
        "annual_rate": annual_rate,
        "start_date": start_date,
        "end_date": end_date,
        "as_of": today,
        "is_matured": is_matured,
    }


def valuation(asset: dict, price_map: dict, fx_rate: dict | None) -> dict | None:
    """valuator 공통 인터페이스 구현 — 정기예금 평가.

    매칭 조건: ticker_name in DEPOSIT_TICKER_NAMES AND ticker_code "rate|date|date" 패턴.
    """
    ticker_name = asset.get("ticker_name")
    ticker_code = asset.get("ticker_code")
    if not is_deposit(ticker_name, ticker_code):
        return None

    meta = parse_deposit_metadata(ticker_code) or {}
    avg_price = float(asset.get("avg_price", 0.0) or 0.0)
    quantity = float(asset.get("quantity", 0.0) or 0.0)
    buy_amount = avg_price * quantity

    try:
        dep = calculate_deposit_valuation(
            principal=buy_amount,
            annual_rate=meta.get("annual_rate", 0.0),
            start_date=meta.get("start_date"),
            end_date=meta.get("end_date"),
        )
    except Exception:
        return None

    from core.calculator import _safe_pnl_rate  # 순환 import 회피용 lazy import

    valuation_amount = dep["valuation_amount"]
    profit = dep["profit"]
    pnl_rate = _safe_pnl_rate(profit, buy_amount)

    return {
        **asset,
        "current_price": avg_price,  # 정기예금은 단가 개념 부재
        "price_date": dep["as_of"],
        "price_source": "deposit",
        "buy_amount": buy_amount,
        "valuation_amount": valuation_amount,
        "profit": profit,
        "pnl_rate": pnl_rate,
        "_deposit": {
            "annual_rate": dep["annual_rate"],
            "start_date": dep["start_date"],
            "end_date": dep["end_date"],
            "elapsed_days": dep["elapsed_days"],
            "interest_gross": dep["interest_gross"],
            "is_matured": dep["is_matured"],
        },
    }
