"""
core/calculator.py
- 거래 원장(transactions) + 일별 종가 캐시(daily_prices)를 결합하여
  종목별 / 계좌별 / 전체 포트폴리오 평가액 및 수익률을 계산한다.
- 텔레그램 /status 명령어에서 사용할 사람이 읽기 쉬운 리포트 문자열을 생성한다.

데이터 흐름:
    transactions (원장) -> holdings (잔고 집계)
        -> daily_prices (최신 종가 매핑, 없으면 평단가 fallback)
        -> 종목별 enriched dict -> 계좌별 그룹화 -> 포맷 문자열 (청크 단위)
"""

from collections import OrderedDict
from contextlib import suppress
from datetime import date, datetime

from database.connection import get_connection

# ----------------------------------------------------------------------
# 0. 정기예금 식별 및 평가액 산출
# ----------------------------------------------------------------------
# 정기예금은 ticker_name 이 다음 값들 중 하나이고,
# ticker_code 가 "이율|시작일|만기일" 형태일 때 식별.
# 예) ticker_code="4.42|2023-01-02|2028-01-02"
DEPOSIT_TICKER_NAMES = ("정기예금", "예금")
DEPOSIT_CODE_PARTS = 3  # 이율 | 시작일 | 만기일


def is_deposit(ticker_name: str | None, ticker_code: str | None) -> bool:
    """정기예금/예금 자산 여부 판별.

    조건:
      1) ticker_name 이 DEPOSIT_TICKER_NAMES 중 하나
      2) ticker_code 가 "이율|YYYY-MM-DD|YYYY-MM-DD" 형태 (3-part 파싱 가능)
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

    Raises:
        ValueError: 날짜 포맷이 잘못된 경우
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

    Args:
        principal: 가입 원금
        annual_rate: 연이율 (%, 예: 4.42)
        start_date: 가입일
        end_date: 만기일 (경과일수의 상한)
        today: 평가 기준일 (None이면 date.today())

    Returns:
        {
          "principal": float,
          "elapsed_days": int,     # 경과일수 (만기 후에는 만기일까지)
          "interest_gross": float, # 세전 이자
          "valuation_amount": float,  # 원금 + 세전 이자 (현재 평가액)
          "profit": float,         # = 세전 이자
          "annual_rate": float,
          "start_date": date,
          "end_date": date,
          "as_of": date,           # 실제 평가 기준일
          "is_matured": bool,      # 만기 경과 여부
        }
    """
    if today is None:
        today = date.today()

    # 경과일수 = (오늘 - 시작일).days. 시작 전이면 0.
    raw_elapsed = (today - start_date).days
    if raw_elapsed < 0:
        raw_elapsed = 0

    # 만기 경과 시 만기일까지로 상한
    max_elapsed = (end_date - start_date).days
    if max_elapsed < 0:
        max_elapsed = 0
    elapsed_days = min(raw_elapsed, max_elapsed)
    is_matured = raw_elapsed >= max_elapsed and max_elapsed > 0

    # 세전 이자 = 원금 * (연이율 / 100) * (경과일수 / 365)
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


# ----------------------------------------------------------------------
# 1. daily_prices 조회 (ticker_code -> 최신 close_price)
# ----------------------------------------------------------------------
def get_latest_prices_map() -> dict:
    """
    daily_prices 테이블에서 ticker_code 별로 가장 최신 price_date의 close_price를
    {ticker_code: {"price_date": date, "close_price": float}} 형태로 반환.

    - ticker_code 가 동일하지만 서로 다른 price_date row가 여러 개일 때
      MAX(price_date) 만 사용 (시계열 캐시이므로 항상 가장 최신 1 row 만 필요).
    - daily_prices 가 비어있으면 빈 dict 반환.
    - 환율 티커('USD/KRW')도 함께 반환된다.
    """
    conn = get_connection()
    if not conn:
        return {}

    query = """
        SELECT t.ticker_code, t.price_date, t.close_price
        FROM daily_prices t
        INNER JOIN (
            SELECT ticker_code, MAX(price_date) AS max_date
            FROM daily_prices
            GROUP BY ticker_code
        ) latest
          ON t.ticker_code = latest.ticker_code
         AND t.price_date  = latest.max_date
    """
    try:
        cur = conn.cursor()
        cur.execute(query)
        rows = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ daily_prices 조회 실패: {e}")
        with suppress(Exception):
            conn.close()
        return {}

    price_map: dict = {}
    for ticker_code, price_date, close_price in rows:
        price_map[str(ticker_code)] = {
            "price_date": price_date,
            "close_price": float(close_price),
        }
    return price_map


# ----------------------------------------------------------------------
# 1-b. 환율 헬퍼 (해외주식 KRW 환산용)
# ----------------------------------------------------------------------
FX_USD_KRW = "USD/KRW"  # core.price_fetcher.FX_TICKER 와 동일


def get_latest_fx_rate(fx_ticker: str = FX_USD_KRW) -> dict | None:
    """가장 최신 USD/KRW 환율 1건 조회.

    Returns:
        {"price_date": date, "rate": float (KRW per USD)} 또는 None.
    """
    conn = get_connection()
    if not conn:
        return None
    query = """
        SELECT price_date, close_price FROM daily_prices
        WHERE ticker_code = ?
        ORDER BY price_date DESC LIMIT 1
    """
    try:
        cur = conn.cursor()
        cur.execute(query, (fx_ticker,))
        row = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ 환율 조회 실패: {e}")
        with suppress(Exception):
            conn.close()
        return None
    if not row:
        return None
    return {"price_date": row[0], "rate": float(row[1])}


# ----------------------------------------------------------------------
# 2. holdings + 가격 매핑 (계산 핵심)
# ----------------------------------------------------------------------
def _safe_pnl_rate(profit: float, buy_amount: float) -> float:
    """수익률(%) 계산 헬퍼. 분모 0 보호."""
    if buy_amount <= 0:
        return 0.0
    return (profit / buy_amount) * 100.0


def enrich_holdings_with_prices(
    holdings: list,
    price_map: dict | None = None,
    fx_rate: dict | None = None,
) -> list:
    """
    AssetRepository.get_current_holdings() 결과(holdings list)에
    현재가 / 평가액 / 손익 / 수익률 필드를 더해 반환.

    우선순위:
      1) 정기예금/예금 (ticker_name 매칭 + ticker_code "이율|시작일|만기일")
         -> 일할 계산 평가액, price_source = "deposit"
      2) 해외주식/미국 ETF (영문 1~5자리 티커, KRW 환산)
         -> price_source = "us_stock"
      3) daily_prices[ticker_code] 최신 close_price 매핑 (국내 주식/ETF)
         -> price_source = "market"
      4) 매핑 없음 (펀드/현금 등)
         -> 평단가를 현재가로 사용 (fallback)
    - 매수금액 = avg_price * quantity (USD/KRW 모두 단가 * 수량)
    - 평가금액 = current_price * quantity (USD 종목은 KRW 환산, market 가격 그대로)
    - 손익    = 평가금액 - 매수금액
    - 수익률  = 손익 / 매수금액 * 100
    """
    if price_map is None:
        price_map = get_latest_prices_map()
    if fx_rate is None:
        fx_rate = get_latest_fx_rate()

    enriched: list = []
    for h in holdings:
        ticker_name = h.get("ticker_name")
        ticker_code = h.get("ticker_code")
        avg_price = float(h.get("avg_price", 0.0) or 0.0)
        quantity = float(h.get("quantity", 0.0) or 0.0)

        buy_amount = avg_price * quantity

        # --- 1) 정기예금 분기: ticker_name + "이율|시작일|만기일" 매칭 ---
        if is_deposit(ticker_name, ticker_code):
            meta = parse_deposit_metadata(ticker_code) or {}
            principal = buy_amount  # avg_price * quantity == 가입원금
            try:
                dep = calculate_deposit_valuation(
                    principal=principal,
                    annual_rate=meta.get("annual_rate", 0.0),
                    start_date=meta.get("start_date"),
                    end_date=meta.get("end_date"),
                )
            except Exception as e:
                print(f"⚠️ 정기예금 평가 계산 실패 ({ticker_code}): {e}")
                dep = None

            if dep:
                valuation_amount = dep["valuation_amount"]
                profit = dep["profit"]
                pnl_rate = _safe_pnl_rate(profit, buy_amount)
                # current_price 는 '1원 단가' 표시 (정기예금은 단가 개념이 없음)
                current_price = avg_price
                enriched.append(
                    {
                        **h,
                        "current_price": current_price,
                        "price_date": dep["as_of"],
                        "price_source": "deposit",
                        "buy_amount": buy_amount,
                        "valuation_amount": valuation_amount,
                        "profit": profit,
                        "pnl_rate": pnl_rate,
                        # 정기예금 전용 메타 (디버그/표시용)
                        "_deposit": {
                            "annual_rate": dep["annual_rate"],
                            "start_date": dep["start_date"],
                            "end_date": dep["end_date"],
                            "elapsed_days": dep["elapsed_days"],
                            "interest_gross": dep["interest_gross"],
                            "is_matured": dep["is_matured"],
                        },
                    }
                )
                continue
            # 파싱 실패 시 fallback 으로 진행

        # --- 2) 해외주식 분기 (영문 1~5자리 + 환율 매핑) ---
        # _US_TICKER_PATTERN 을 price_fetcher 에서 가져오지 않도록
        # core 내부에서는 별도 정규식으로 판별 (loose 결합).
        from core.price_fetcher import is_us_stock_ticker

        if is_us_stock_ticker(ticker_code) and fx_rate:
            usd_info = price_map.get(str(ticker_code))
            if usd_info:
                usd_close = float(usd_info["close_price"])  # USD per share
                krw_per_usd = float(fx_rate["rate"])
                current_price_krw = usd_close * krw_per_usd  # KRW per share
                valuation_amount = current_price_krw * quantity
                # avg_price 는 USD 기준이므로 KRW 로 환산해서 비교
                buy_amount_krw = buy_amount * krw_per_usd
                profit = valuation_amount - buy_amount_krw
                pnl_rate = _safe_pnl_rate(profit, buy_amount_krw)
                enriched.append(
                    {
                        **h,
                        "current_price": current_price_krw,  # KRW 환산 단가
                        "price_date": usd_info["price_date"],
                        "price_source": "us_stock",
                        # buy_amount 는 KRW 환산값으로 통일 (집계/표시 일관성)
                        "buy_amount": buy_amount_krw,
                        "valuation_amount": valuation_amount,
                        "profit": profit,
                        "pnl_rate": pnl_rate,
                        # 표시/디버그용 추가 메타
                        "_us": {
                            "usd_close": usd_close,
                            "krw_per_usd": krw_per_usd,
                            "fx_date": fx_rate["price_date"],
                            "buy_amount_usd": buy_amount,  # 원래 USD 매수액
                        },
                    }
                )
                continue
            # USD 시세 없으면 아래 market 분기로 진행 (현재는 fallback)

        # --- 3) daily_prices 매핑 (국내 주식/ETF) ---
        price_info = price_map.get(str(ticker_code)) if ticker_code else None
        if price_info:
            current_price = float(price_info["close_price"])
            price_date = price_info["price_date"]
            price_source = "market"
        else:
            # 4) fallback: 평단가를 현재가로 사용 (1배수)
            current_price = avg_price
            price_date = None
            price_source = "fallback"

        valuation_amount = current_price * quantity
        profit = valuation_amount - buy_amount
        pnl_rate = _safe_pnl_rate(profit, buy_amount)

        enriched.append(
            {
                **h,
                "current_price": current_price,
                "price_date": price_date,
                "price_source": price_source,
                "buy_amount": buy_amount,
                "valuation_amount": valuation_amount,
                "profit": profit,
                "pnl_rate": pnl_rate,
            }
        )
    return enriched


# ----------------------------------------------------------------------
# 3. 계좌별 집계
# ----------------------------------------------------------------------
def group_holdings_by_account(enriched_holdings: list) -> "OrderedDict[str, list]":
    """enriched holdings 를 account_name 기준으로 그룹화하여 OrderedDict 로 반환."""
    grouped: OrderedDict[str, list] = OrderedDict()
    for h in enriched_holdings:
        account = h.get("account_name", "미분류")
        grouped.setdefault(account, []).append(h)
    return grouped


def summarize_accounts(grouped: "OrderedDict[str, list]") -> list:
    """
    계좌별 소계 dict 리스트 반환:
      [{ account_name, buy_amount, valuation_amount, profit, pnl_rate, count }, ...]
    입력 그룹화 dict 의 키 순서를 그대로 유지.
    """
    summaries: list = []
    for account, items in grouped.items():
        buy_amount = sum(it["buy_amount"] for it in items)
        valuation_amount = sum(it["valuation_amount"] for it in items)
        profit = valuation_amount - buy_amount
        summaries.append(
            {
                "account_name": account,
                "buy_amount": buy_amount,
                "valuation_amount": valuation_amount,
                "profit": profit,
                "pnl_rate": _safe_pnl_rate(profit, buy_amount),
                "count": len(items),
            }
        )
    return summaries


def summarize_total(enriched_holdings: list) -> dict:
    """전체 포트폴리오 합계 dict."""
    buy_amount = sum(it["buy_amount"] for it in enriched_holdings)
    valuation_amount = sum(it["valuation_amount"] for it in enriched_holdings)
    profit = valuation_amount - buy_amount
    return {
        "buy_amount": buy_amount,
        "valuation_amount": valuation_amount,
        "profit": profit,
        "pnl_rate": _safe_pnl_rate(profit, buy_amount),
        "count": len(enriched_holdings),
    }


# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# CLI 진입점
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from core.formatter import build_status_chunks

    chunks = build_status_chunks()
    print(f"=== 청크 {len(chunks)}개로 분할됨 ===\n")
    for i, c in enumerate(chunks, 1):
        print(f"--- [chunk {i}] 길이 {len(c)} ---")
        print(c)
        print()
