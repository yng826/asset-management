from datetime import datetime
from database.connection import get_connection


class AssetRepository:
    def __init__(self):
        pass

    def add_transaction(self, data: dict, raw_memo: str = None) -> bool:
        """파싱된 트랜잭션 데이터를 transactions 테이블에 추가"""
        conn = get_connection()
        if not conn:
            return False

        query = """
            INSERT INTO transactions (
                trans_date, account_name, ticker_name, ticker_code,
                action_type, quantity, unit_price, total_amount, memo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        # 날짜 없으면 오늘 날짜
        trans_date = data.get("trans_date") or datetime.now().strftime("%Y-%m-%d")

        params = (
            trans_date,
            data.get("account_name"),
            data.get("ticker_name"),
            data.get("ticker_code"),
            data.get("action_type"),
            data.get("quantity", 0.0),
            data.get("unit_price", 0.0),
            data.get("total_amount", 0.0),
            raw_memo,
        )

        try:
            cur = conn.cursor()
            cur.execute(query, params)
            cur.close()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 트랜잭션 저장 실패: {e}")
            conn.close()
            return False

    def get_current_holdings(self):
        """
        거래 원장을 바탕으로 종목별 보유 수량 및 매수 평단가 집계
        - 보유수량 = 매수수량합 - 매도수량합
        - 총매수금 = BUY 거래의 total_amount 합
        - 총매수량 = BUY 거래의 quantity 합
        - 평단가 = 총매수금 / 총매수량
        """
        conn = get_connection()
        if not conn:
            return []

        query = """
            SELECT 
                account_name,
                ticker_name,
                ticker_code,
                SUM(CASE WHEN action_type = 'BUY' THEN quantity 
                        WHEN action_type = 'SELL' THEN -quantity 
                        ELSE 0 END) AS current_qty,
                SUM(CASE WHEN action_type = 'BUY' THEN total_amount ELSE 0 END) AS total_buy_amount,
                SUM(CASE WHEN action_type = 'BUY' THEN quantity ELSE 0 END) AS total_buy_qty
            FROM transactions
            WHERE action_type IN ('BUY', 'SELL')
            GROUP BY account_name, ticker_name, ticker_code
            HAVING current_qty > 0
        """

        try:
            cur = conn.cursor()
            cur.execute(query)
            rows = cur.fetchall()
            cur.close()
            conn.close()

            holdings = []
            for row in rows:
                acc, name, code, qty, buy_amt, buy_qty = row
                avg_price = (buy_amt / buy_qty) if buy_qty > 0 else 0
                holdings.append(
                    {
                        "account_name": acc,
                        "ticker_name": name,
                        "ticker_code": code,
                        "quantity": float(qty),
                        "avg_price": float(avg_price),
                    }
                )
            return holdings
        except Exception as e:
            print(f"❌ 보유 종목 조회 실패: {e}")
            conn.close()
            return []

    def get_total_dividends(self, year: int = None) -> float:
        """누적 배당금 조회 (특정 연도 지정 가능)"""
        conn = get_connection()
        if not conn:
            return 0.0

        query = (
            "SELECT SUM(total_amount) FROM transactions WHERE action_type = 'DIVIDEND'"
        )
        params = []
        if year:
            query += " AND YEAR(trans_date) = ?"
            params.append(year)

        try:
            cur = conn.cursor()
            cur.execute(query, params)
            result = cur.fetchone()[0] or 0.0
            cur.close()
            conn.close()
            return float(result)
        except Exception as e:
            print(f"❌ 배당금 조회 실패: {e}")
            conn.close()
            return 0.0


if __name__ == "__main__":
    # 레포지토리 테스트: 가짜 데이터 1건 넣고 조회해보기
    repo = AssetRepository()
    sample_data = {
        "trans_date": "2026-09-04",
        "account_name": "토스증권기본계좌",
        "ticker_name": "삼성전자",
        "ticker_code": "005930",
        "action_type": "BUY",
        "quantity": 5.0,
        "unit_price": 71000.0,
        "total_amount": 355000.0,
    }

    print("1. 테스트 거래 데이터 INSERT 시도...")
    success = repo.add_transaction(sample_data, raw_memo="토스 삼전 5주 71000원 매수")
    print(f"결과: {'성공' if success else '실패'}")

    print("\n2. 현재 보유 잔고 집계 조회:")
    holdings = repo.get_current_holdings()
    for h in holdings:
        print(
            f" - [{h['account_name']}] {h['ticker_name']}: {h['quantity']}주 (평단가: {h['avg_price']:,.0f}원)"
        )
