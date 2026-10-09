from datetime import datetime
from decimal import Decimal
from typing import Any

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
                action_type, quantity, unit_price, total_amount, currency, memo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        # 날짜 없으면 오늘 날짜
        trans_date = data.get("trans_date") or datetime.now().strftime("%Y-%m-%d")
        currency = data.get("currency") or "KRW"
        action_type = data.get("action_type")
        ticker_name = data.get("ticker_name")
        ticker_code = data.get("ticker_code")
        quantity = data.get("quantity")

        if action_type in ("DEPOSIT", "WITHDRAW"):
            if not ticker_name:
                if currency == "USD":
                    ticker_name = "USD_CASH"
                    ticker_code = "CASH_USD"
                else:
                    ticker_name = "KRW_CASH"
                    ticker_code = "CASH_KRW"
            if not ticker_code:
                if currency == "USD":
                    ticker_code = "CASH_USD"
                else:
                    ticker_code = "CASH_KRW"
            if quantity is None:
                quantity = 0.0
        else:
            if quantity is None:
                quantity = 0.0

        unit_price = data.get("unit_price") if data.get("unit_price") is not None else 0.0
        total_amount = data.get("total_amount") if data.get("total_amount") is not None else 0.0

        params = (
            trans_date,
            data.get("account_name"),
            ticker_name,
            ticker_code,
            action_type,
            quantity,
            unit_price,
            total_amount,
            currency,
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

    def get_holdings_as_of_date(self, target_date: str) -> list:
        """
        특정 날짜(target_date) 기준으로 보유 수량 및 매수 평단가 집계.
        target_date 이전의 모든 거래 내역을 반영하여 계산합니다.
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
            WHERE trans_date <= ? AND action_type IN ('BUY', 'SELL', 'DEPOSIT', 'WITHDRAW', 'DIVIDEND')
            GROUP BY account_name, ticker_name, ticker_code
            HAVING current_qty > 0 OR (account_name LIKE '%CASH%' AND current_qty = 0)
        """

        try:
            cur = conn.cursor()
            cur.execute(query, (target_date,))
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
            print(f"❌ {target_date} 기준 보유 종목 조회 실패: {e}")
            conn.close()
            return []

    def get_cash_balances_by_currency(self, target_date: str) -> dict:
        """
        기준일(target_date) 시점까지의 transactions 원장을 기반으로 통화별(currency) 순현금 잔액 산출.
        현금 흐름 계산식: DEPOSIT + SELL + DIVIDEND - BUY - WITHDRAW (초기 보유분은 계좌별 "초기 현금 보정" DEPOSIT 으로 상쇄)
        """
        conn = get_connection()
        if not conn:
            return {}

        query = """
            SELECT
                COALESCE(currency, 'KRW') AS currency,
                SUM(
                    CASE
                        WHEN UPPER(action_type) IN ('DEPOSIT', 'SELL', 'DIVIDEND') THEN total_amount
                        WHEN UPPER(action_type) IN ('BUY', 'WITHDRAW') THEN -total_amount
                        ELSE 0
                    END
                ) AS net_cash
            FROM transactions
            WHERE trans_date <= ?
            GROUP BY COALESCE(currency, 'KRW')
        """
        try:
            cur = conn.cursor()
            cur.execute(query, (target_date,))
            rows = cur.fetchall()
            cur.close()
            conn.close()

            balances = {}
            for currency, balance in rows:
                balances[currency] = float(balance or 0.0)
            return balances
        except Exception as e:
            print(f"❌ {target_date} 기준 통화별 현금 잔액 조회 실패: {e}")
            conn.close()
            return {}

    def get_account_cash_balances(self, target_date: str) -> list[dict]:
        """
        기준일(target_date) 시점까지의 transactions 원장을 기반으로 계좌별·통화별 순현금 잔액 산출.
        """
        conn = get_connection()
        if not conn:
            return []

        query = """
            SELECT
                COALESCE(account_name, '기본계좌') AS account_name,
                COALESCE(currency, 'KRW') AS currency,
                SUM(
                    CASE
                        WHEN UPPER(action_type) IN ('DEPOSIT', 'SELL', 'DIVIDEND') THEN total_amount
                        WHEN UPPER(action_type) IN ('BUY', 'WITHDRAW') THEN -total_amount
                        ELSE 0
                    END
                ) AS net_cash
            FROM transactions
            WHERE trans_date <= ?
            GROUP BY account_name, COALESCE(currency, 'KRW')
            HAVING net_cash != 0
        """
        try:
            cur = conn.cursor()
            cur.execute(query, (target_date,))
            rows = cur.fetchall()
            cur.close()
            conn.close()

            results = []
            for row in rows:
                acc_name, currency, net_cash = row
                results.append(
                    {
                        "account_name": acc_name,
                        "currency": currency,
                        "net_cash": float(net_cash or 0.0),
                    }
                )
            return results
        except Exception as e:
            print(f"❌ {target_date} 기준 계좌별 현금 잔액 조회 실패: {e}")
            conn.close()
            return []

    def get_total_dividends(self, year: int = None) -> float:
        """누적 배당금 조회 (특정 연도 지정 가능)"""
        conn = get_connection()
        if not conn:
            return 0.0

        query = "SELECT SUM(total_amount) FROM transactions WHERE action_type = 'DIVIDEND'"
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

    def get_recent_transactions(self, limit: int = 10) -> list:
        """최근 거래 내역을 조회"""
        conn = get_connection()
        if not conn:
            return []

        query = """
            SELECT
                trans_date, account_name, ticker_name, action_type,
                quantity, unit_price, total_amount, memo
            FROM transactions
            ORDER BY trans_date DESC, id DESC
            LIMIT ?
        """

        try:
            cur = conn.cursor()
            cur.execute(query, (limit,))
            rows = cur.fetchall()
            cur.close()
            conn.close()

            transactions = []
            for row in rows:
                (
                    trans_date,
                    account_name,
                    ticker_name,
                    action_type,
                    quantity,
                    unit_price,
                    total_amount,
                    memo,
                ) = row
                transactions.append(
                    {
                        "trans_date": trans_date,
                        "account_name": account_name,
                        "ticker_name": ticker_name,
                        "action_type": action_type,
                        "quantity": float(quantity),
                        "unit_price": float(unit_price),
                        "total_amount": float(total_amount),
                        "memo": memo,
                    }
                )
            return transactions
        except Exception as e:
            print(f"❌ 최근 거래 내역 조회 실패: {e}")
            conn.close()
            return []

    def save_snapshot(self, snapshot_date: str, data: dict) -> bool:
        """일별 총자산 스냅샷 저장 (UPSERT)"""
        conn = get_connection()
        if not conn:
            return False

        query = """
            INSERT INTO daily_snapshots (
                snapshot_date, total_eval_amount, total_invested_amount, cash_amount
            ) VALUES (?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
                total_eval_amount = VALUES(total_eval_amount),
                total_invested_amount = VALUES(total_invested_amount),
                cash_amount = VALUES(cash_amount)
        """

        params = (
            snapshot_date,
            data.get("total_eval_amount", 0.0),
            data.get("total_invested_amount", 0.0),
            data.get("cash_amount", 0.0),
        )

        try:
            cur = conn.cursor()
            cur.execute(query, params)
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ 스냅샷 저장 실패: {e}")
            conn.close()
            return False

    def get_daily_pnl_history(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict[str, Any]]:
        """LAG() 윈도우 함수를 사용해 daily_snapshots 기반 일자별 손익 및 일일 수익률 산출"""
        conn = get_connection()
        if not conn:
            return []

        # 서브쿼리에서 LAG()로 전일 데이터를 구한 뒤, 바깥에서 기간 필터링 및 수익률 연산
        query = """
            SELECT
                snapshot_date,
                total_eval_amount AS total_eval,
                net_inflow,
                daily_pnl,
                ROUND(
                    CASE
                        WHEN prev_eval IS NULL THEN 0.0
                        WHEN (prev_eval + IF(net_inflow > 0, net_inflow, 0)) > 0
                        THEN (daily_pnl / (prev_eval + IF(net_inflow > 0, net_inflow, 0))) * 100
                        ELSE 0.0
                    END, 2
                ) AS daily_return_pct,
                SUM(daily_pnl) OVER (ORDER BY snapshot_date ASC) AS cumulative_pnl
            FROM (
                SELECT
                    snapshot_date,
                    total_eval_amount,
                    total_invested_amount,
                    LAG(total_eval_amount) OVER (ORDER BY snapshot_date ASC) AS prev_eval,
                    (total_invested_amount - LAG(total_invested_amount) OVER (ORDER BY snapshot_date ASC)) AS net_inflow,
                    COALESCE(
                        (total_eval_amount - LAG(total_eval_amount) OVER (ORDER BY snapshot_date ASC))
                        - (total_invested_amount - LAG(total_invested_amount) OVER (ORDER BY snapshot_date ASC)),
                        0.0
                    ) AS daily_pnl
                FROM daily_snapshots
                ORDER BY snapshot_date ASC
            ) t
            WHERE (%s IS NULL OR snapshot_date >= %s)
            AND (%s IS NULL OR snapshot_date <= %s)
            ORDER BY snapshot_date DESC
        """
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(query, (start_date, start_date, end_date, end_date))
            rows = cur.fetchall()
            # Decimal -> float 변환
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, Decimal):
                        r[k] = float(v)
                    elif hasattr(v, "isoformat"):
                        r[k] = str(v)
            return rows
        finally:
            cur.close()
            conn.close()

    def get_latest_asset_class_summary(self) -> list[dict]:
        """
        v_latest_asset_breakdown 뷰를 직접 조회하여 최신 자산군별 비중 리포트 반환.
        """
        conn = get_connection()
        if not conn:
            return []

        query = """
            SELECT
                asset_class,
                class_eval,
                eval_diff,
                diff_pct,
                weight_pct
            FROM v_latest_asset_breakdown
            ORDER BY class_eval DESC
        """

        try:
            cur = conn.cursor()
            cur.execute(query)
            columns = [col[0] for col in cur.description]
            rows = [dict(zip(columns, r, strict=False)) for r in cur.fetchall()]
            cur.close()
            conn.close()
            return rows
        except Exception as e:
            print(f"❌ 자산군 요약 조회 실패: {e}")
            conn.close()
            return []

    def get_holding_snapshot_pair(self, snapshot_date: str) -> list[dict]:
        """snapshot_date 와 그 직전 스냅샷일의 계좌·종목별 세부 스냅샷 (일자별 손익 분해용).

        Returns:
            [{snapshot_date(str), account_name, ticker_code, ticker_name, eval_amount, invested_amount}, ...]
        """
        conn = get_connection()
        if not conn:
            return []

        query = """
            SELECT
                h.snapshot_date,
                h.account_name,
                h.ticker_code,
                (SELECT MAX(t.ticker_name) FROM transactions t WHERE t.ticker_code = h.ticker_code) AS ticker_name,
                h.eval_amount,
                h.invested_amount
            FROM daily_holding_snapshots h
            WHERE h.snapshot_date = ?
               OR h.snapshot_date = (
                   SELECT MAX(snapshot_date) FROM daily_holding_snapshots WHERE snapshot_date < ?
               )
        """
        try:
            cur = conn.cursor()
            cur.execute(query, (snapshot_date, snapshot_date))
            rows = cur.fetchall()
            cur.close()
            return [
                {
                    "snapshot_date": str(r[0]),
                    "account_name": r[1],
                    "ticker_code": r[2],
                    "ticker_name": r[3],
                    "eval_amount": float(r[4] or 0.0),
                    "invested_amount": float(r[5] or 0.0),
                }
                for r in rows
            ]
        except Exception as e:
            print(f"❌ {snapshot_date} 세부 스냅샷 조회 실패: {e}")
            return []
        finally:
            conn.close()

    def has_batch_run(self, batch_name: str, execution_date: str) -> bool:
        """해당 일자에 배치(batch_name)가 실행·기록되었는지 여부 (예: closing_1600 일일 결산)"""
        conn = get_connection()
        if not conn:
            return False
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM batch_execution_logs WHERE batch_name = ? AND execution_date = ?",
                (batch_name, execution_date),
            )
            (count,) = cur.fetchone()
            cur.close()
            return count > 0
        except Exception as e:
            print(f"❌ 배치 실행 기록 조회 실패: {e}")
            return False
        finally:
            conn.close()

    def record_batch_audit_log(self, batch_name: str, message: str = "") -> bool:
        """스케줄러 실행 직후 현재 DB 적재 현황을 집계하여 감사 로그 테이블에 자동 기록"""
        conn = get_connection()
        if not conn:
            return False

        today = datetime.now().strftime("%Y-%m-%d")

        # 1. 당일 자산군별 적재 카운트 및 스냅샷 여부 원샷 조회
        check_query = """
            SELECT
                SUM(CASE WHEN ticker_code REGEXP '^[A-Z]{1,5}$' AND ticker_code NOT IN ('USD', 'KRW') THEN 1 ELSE 0 END) AS us_cnt,
                SUM(CASE WHEN ticker_code = 'USD/KRW' THEN 1 ELSE 0 END) AS fx_cnt,
                SUM(CASE WHEN ticker_code REGEXP '^(KR5|K55)' THEN 1 ELSE 0 END) AS fund_cnt,
                SUM(CASE WHEN ticker_code REGEXP '^[0-9]{6}$' THEN 1 ELSE 0 END) AS kr_cnt,
                SUM(CASE WHEN ticker_code LIKE 'KRW-%' THEN 1 ELSE 0 END) AS crypto_cnt
            FROM daily_prices
            WHERE price_date = %s;
        """

        snapshot_query = "SELECT COUNT(*) AS cnt FROM daily_snapshots WHERE snapshot_date = %s;"

        insert_query = """
            INSERT INTO batch_execution_logs (
                batch_name, execution_date, status,
                us_count, fx_count, fund_count, kr_count, crypto_count,
                snapshot_created, message
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
        """

        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(check_query, (today,))
            p_row = cur.fetchone() or {}

            cur.execute(snapshot_query, (today,))
            s_row = cur.fetchone() or {}

            us_cnt = int(p_row.get("us_cnt") or 0)
            fx_cnt = int(p_row.get("fx_cnt") or 0)
            fund_cnt = int(p_row.get("fund_cnt") or 0)
            kr_cnt = int(p_row.get("kr_cnt") or 0)
            crypto_cnt = int(p_row.get("crypto_cnt") or 0)
            snapshot_ok = 1 if int(s_row.get("cnt") or 0) > 0 else 0

            # 정상 여부 판별 상태값 도출
            if batch_name == "morning_1030":
                status = "SUCCESS" if (fx_cnt > 0 and fund_cnt > 0) else "WARNING"
            elif batch_name == "closing_1600":
                status = "SUCCESS" if (kr_cnt > 0 and snapshot_ok == 1) else "WARNING"
            else:
                status = "INFO"

            cur.execute(
                insert_query,
                (
                    batch_name,
                    today,
                    status,
                    us_cnt,
                    fx_cnt,
                    fund_cnt,
                    kr_cnt,
                    crypto_cnt,
                    snapshot_ok,
                    message,
                ),
            )
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"❌ 감사 로그 적재 실패: {e}")
            return False
        finally:
            cur.close()
            conn.close()

    def save_holding_snapshots(self, snapshot_date: str, holdings: list[dict]) -> bool:
        """
        일별 종목별 보유 스냅샷을 해당 일자 단위로 교체 적재한다.
        - 같은 일자의 기존 행을 먼저 지우고 적재 (계좌명 변경·전량 매도 시 옛 행 잔존 방지)
        """
        conn = get_connection()
        if not conn:
            return False

        query = """
            INSERT INTO daily_holding_snapshots (
                snapshot_date, account_name, ticker_code, quantity,
                close_price, eval_amount, invested_amount
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                quantity = VALUES(quantity),
                close_price = VALUES(close_price),
                eval_amount = VALUES(eval_amount),
                invested_amount = VALUES(invested_amount)
        """

        params = [
            (
                snapshot_date,
                h["account_name"],
                h["ticker_code"],
                h["quantity"],
                h.get("close_price") or h.get("current_price") or 0.0,
                # 표준 컬럼명 우선 참조 후 레거시 키 fallback
                h.get("eval_amount") or h.get("valuation_amount") or 0.0,
                h.get("invested_amount") or h.get("buy_amount") or 0.0,
            )
            for h in holdings
        ]

        try:
            cur = conn.cursor()
            # autocommit 커넥션이므로 삭제·적재를 하나의 트랜잭션으로 묶음
            conn.autocommit = False
            cur.execute("DELETE FROM daily_holding_snapshots WHERE snapshot_date = %s", (snapshot_date,))
            if params:
                cur.executemany(query, params)
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ 보유 종목 스냅샷 저장 실패: {e}")
            conn.rollback()
            return False
        finally:
            cur.close()
            conn.close()

    def get_detector_settings(self) -> dict[str, float]:
        """이상징후 감시 기준값(detector_settings) 전체 조회. 실패 시 빈 dict 반환."""
        conn = get_connection()
        if not conn:
            return {}

        try:
            cur = conn.cursor()
            cur.execute("SELECT setting_key, setting_value FROM detector_settings")
            rows = cur.fetchall()
            cur.close()
            return {key: float(value) for key, value in rows}
        except Exception as e:
            print(f"❌ 이상징후 기준값 조회 실패: {e}")
            return {}
        finally:
            conn.close()

    def get_last_anomaly_alert_pct(self, alert_date, ticker_code: str, event_type: str) -> float | None:
        """해당 일자·종목·이벤트로 마지막 발송한 등락률 조회 (발송 이력 없으면 None)"""
        conn = get_connection()
        if not conn:
            return None

        query = """
            SELECT change_pct FROM anomaly_alert_logs
            WHERE alert_date = %s AND ticker_code = %s AND event_type = %s
        """
        try:
            cur = conn.cursor()
            cur.execute(query, (alert_date, ticker_code, event_type))
            row = cur.fetchone()
            cur.close()
            return float(row[0]) if row else None
        except Exception as e:
            print(f"❌ 이상징후 발송 이력 조회 실패: {e}")
            return None
        finally:
            conn.close()

    def save_anomaly_alert(self, alert_date, ticker_code: str, event_type: str, change_pct: float) -> bool:
        """이상징후 알림 발송 기록 UPSERT (같은 일자·종목·이벤트는 마지막 등락률로 갱신)"""
        conn = get_connection()
        if not conn:
            return False

        query = """
            INSERT INTO anomaly_alert_logs (alert_date, ticker_code, event_type, change_pct)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                change_pct = VALUES(change_pct),
                alert_count = alert_count + 1
        """
        try:
            cur = conn.cursor()
            cur.execute(query, (alert_date, ticker_code, event_type, round(change_pct, 2)))
            cur.close()
            return True
        except Exception as e:
            print(f"❌ 이상징후 발송 기록 저장 실패: {e}")
            return False
        finally:
            conn.close()


if __name__ == "__main__":
    # 레포지토리 테스트: 가짜 데이터 1건 넣고 조회해보기
    repo = AssetRepository()
    sample_data = {
        "trans_date": "2026-09-04",
        "account_name": "토스 일반",
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
