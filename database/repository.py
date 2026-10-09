import functools
from datetime import datetime
from decimal import Decimal
from typing import Any

from database.connection import execute, fetch_all, get_connection


def _on_error(default, message):
    """
    조회·저장 중 예외가 나면 '❌ {message}: {예외}' 출력 후 default 반환 (호출부는 예외 대신 기본값을 받음).
    - default: 값 또는 list/dict 같은 생성자 (호출마다 새 객체)
    - message: 문자열 또는 메서드 인자를 받아 문자열을 만드는 함수
    DB 연결 실패는 fetch_all/execute 가 빈 결과/False 로 처리하므로 메시지 없이 같은 기본값이 된다.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                text = message(*args, **kwargs) if callable(message) else message
                print(f"❌ {text}: {e}")
                return default() if callable(default) else default

        return wrapper

    return decorator


class AssetRepository:
    def __init__(self):
        pass

    @_on_error(False, "트랜잭션 저장 실패")
    def add_transaction(self, data: dict, raw_memo: str = None) -> bool:
        """파싱된 트랜잭션 데이터를 transactions 테이블에 추가"""
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

        return execute(query, params)

    @_on_error(list, "보유 종목 조회 실패")
    def get_current_holdings(self):
        """
        거래 원장을 바탕으로 종목별 보유 수량 및 매수 평단가 집계
        - 보유수량 = 매수수량합 - 매도수량합
        - 총매수금 = BUY 거래의 total_amount 합
        - 총매수량 = BUY 거래의 quantity 합
        - 평단가 = 총매수금 / 총매수량
        """
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

        holdings = []
        for row in fetch_all(query):
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

    @_on_error(list, "거래 종목 목록 조회 실패")
    def get_traded_tickers(self) -> list[dict]:
        """과거 매수/매도/배당 거래에 등장한 종목 목록 (현금·정기예금 제외, 최근 거래순).

        - 같은 종목코드에 이름이 여러 개면 가장 최근 거래의 이름을 사용 (보유 집계가 이름 단위로 묶이므로 동일 이름 유지용)
        """
        query = """
            SELECT ticker_code, ticker_name, MAX(trans_date) AS last_date
            FROM transactions
            WHERE action_type IN ('BUY', 'SELL', 'DIVIDEND')
              AND ticker_code IS NOT NULL
              AND ticker_code NOT LIKE 'CASH\\_%'
              AND ticker_code NOT LIKE '%|%'
            GROUP BY ticker_code, ticker_name
            ORDER BY last_date DESC
        """

        seen = set()
        tickers = []
        for code, name, _ in fetch_all(query):
            if code in seen:
                continue
            seen.add(code)
            tickers.append({"ticker_code": code, "ticker_name": name})
        return tickers

    @_on_error(False, "ticker_master 테이블 생성 실패")
    def ensure_ticker_master_table(self) -> bool:
        """ticker_master 테이블이 없으면 생성 (schema.sql 8번과 동일 DDL)."""
        return execute(
            """
            CREATE TABLE IF NOT EXISTS ticker_master (
                ticker_code VARCHAR(100) NOT NULL PRIMARY KEY,
                ticker_name VARCHAR(200) NOT NULL,
                market VARCHAR(10) NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_ticker_master_market (market)
            )
            """
        )

    @_on_error(0, "ticker_master 건수 조회 실패")
    def count_ticker_master(self) -> int:
        rows = fetch_all("SELECT COUNT(*) FROM ticker_master")
        return int(rows[0][0]) if rows else 0

    def replace_ticker_master(self, market: str, items: list[tuple[str, str]]) -> bool:
        """시장(market) 단위로 종목 마스터 교체 (삭제 + 일괄 삽입을 한 트랜잭션으로).

        items: [(ticker_code, ticker_name), ...] — 코드 중복 시 먼저 나온 항목 유지
        """
        conn = get_connection()
        if not conn:
            return False
        try:
            conn.autocommit = False
            cur = conn.cursor()
            cur.execute("DELETE FROM ticker_master WHERE market = ?", (market,))
            cur.executemany(
                "INSERT IGNORE INTO ticker_master (ticker_code, ticker_name, market) VALUES (?, ?, ?)",
                [(code, name, market) for code, name in items],
            )
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ ticker_master 교체 실패 ({market}): {e}")
            conn.rollback()
            conn.close()
            return False

    @_on_error(list, "ticker_master 검색 실패")
    def search_ticker_master(self, tokens: list[str], codes: list[str], limit: int = 200) -> list[dict]:
        """종목 마스터 검색: 코드 일치(codes) 또는 공백 제거·대문자 이름에 모든 토큰 포함."""
        conditions = []
        params: list = []
        if codes:
            conditions.append(f"ticker_code IN ({', '.join('?' for _ in codes)})")
            params.extend(codes)
        if tokens:
            like = " AND ".join("REPLACE(UPPER(ticker_name), ' ', '') LIKE ?" for _ in tokens)
            conditions.append(f"({like})")
            params.extend(f"%{t}%" for t in tokens)
        if not conditions:
            return []

        query = f"""
            SELECT ticker_code, ticker_name, market
            FROM ticker_master
            WHERE {" OR ".join(conditions)}
            ORDER BY CHAR_LENGTH(ticker_name)
            LIMIT {int(limit)}
        """
        return [{"code": c, "name": n, "market": m} for c, n, m in fetch_all(query, tuple(params))]

    @_on_error(list, lambda target_date: f"{target_date} 기준 보유 종목 조회 실패")
    def get_holdings_as_of_date(self, target_date: str) -> list:
        """
        특정 날짜(target_date) 기준으로 보유 수량 및 매수 평단가 집계.
        target_date 이전의 모든 거래 내역을 반영하여 계산합니다.
        """
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

        holdings = []
        for row in fetch_all(query, (target_date,)):
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

    @_on_error(dict, lambda target_date: f"{target_date} 기준 통화별 현금 잔액 조회 실패")
    def get_cash_balances_by_currency(self, target_date: str) -> dict:
        """
        기준일(target_date) 시점까지의 transactions 원장을 기반으로 통화별(currency) 순현금 잔액 산출.
        현금 흐름 계산식: DEPOSIT + SELL + DIVIDEND - BUY - WITHDRAW (초기 보유분은 계좌별 "초기 현금 보정" DEPOSIT 으로 상쇄)
        """
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
        balances = {}
        for currency, balance in fetch_all(query, (target_date,)):
            balances[currency] = float(balance or 0.0)
        return balances

    @_on_error(list, lambda target_date: f"{target_date} 기준 계좌별 현금 잔액 조회 실패")
    def get_account_cash_balances(self, target_date: str) -> list[dict]:
        """
        기준일(target_date) 시점까지의 transactions 원장을 기반으로 계좌별·통화별 순현금 잔액 산출.
        """
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
        results = []
        for acc_name, currency, net_cash in fetch_all(query, (target_date,)):
            results.append(
                {
                    "account_name": acc_name,
                    "currency": currency,
                    "net_cash": float(net_cash or 0.0),
                }
            )
        return results

    @_on_error(0.0, "배당금 조회 실패")
    def get_total_dividends(self, year: int = None) -> float:
        """누적 배당금 조회 (특정 연도 지정 가능)"""
        query = "SELECT SUM(total_amount) FROM transactions WHERE action_type = 'DIVIDEND'"
        params = []
        if year:
            query += " AND YEAR(trans_date) = ?"
            params.append(year)

        rows = fetch_all(query, params)
        return float((rows[0][0] if rows else None) or 0.0)

    @_on_error(list, "최근 거래 내역 조회 실패")
    def get_recent_transactions(self, limit: int = 10) -> list:
        """최근 거래 내역을 조회"""
        query = """
            SELECT
                trans_date, account_name, ticker_name, action_type,
                quantity, unit_price, total_amount, memo
            FROM transactions
            ORDER BY trans_date DESC, id DESC
            LIMIT ?
        """

        transactions = []
        for row in fetch_all(query, (limit,)):
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

    @_on_error(False, "스냅샷 저장 실패")
    def save_snapshot(self, snapshot_date: str, data: dict) -> bool:
        """일별 총자산 스냅샷 저장 (UPSERT)"""
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

        return execute(query, params)

    def get_daily_pnl_history(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict[str, Any]]:
        """LAG() 윈도우 함수를 사용해 daily_snapshots 기반 일자별 손익 및 일일 수익률 산출"""
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
        cols = [
            "snapshot_date",
            "total_eval",
            "net_inflow",
            "daily_pnl",
            "daily_return_pct",
            "cumulative_pnl",
        ]
        rows = [
            dict(zip(cols, r, strict=True))
            for r in fetch_all(query, (start_date, start_date, end_date, end_date))
        ]
        # Decimal -> float 변환
        for r in rows:
            for k, v in r.items():
                if isinstance(v, Decimal):
                    r[k] = float(v)
                elif hasattr(v, "isoformat"):
                    r[k] = str(v)
        return rows

    @_on_error(list, "자산군 요약 조회 실패")
    def get_latest_asset_class_summary(self) -> list[dict]:
        """
        v_latest_asset_breakdown 뷰를 직접 조회하여 최신 자산군별 비중 리포트 반환.
        """
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

        columns = ["asset_class", "class_eval", "eval_diff", "diff_pct", "weight_pct"]
        return [dict(zip(columns, r, strict=False)) for r in fetch_all(query)]

    @_on_error(list, lambda snapshot_date: f"{snapshot_date} 세부 스냅샷 조회 실패")
    def get_holding_snapshot_pair(self, snapshot_date: str) -> list[dict]:
        """snapshot_date 와 그 직전 스냅샷일의 계좌·종목별 세부 스냅샷 (일자별 손익 분해용).

        Returns:
            [{snapshot_date(str), account_name, ticker_code, ticker_name, eval_amount, invested_amount}, ...]
        """
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
        return [
            {
                "snapshot_date": str(r[0]),
                "account_name": r[1],
                "ticker_code": r[2],
                "ticker_name": r[3],
                "eval_amount": float(r[4] or 0.0),
                "invested_amount": float(r[5] or 0.0),
            }
            for r in fetch_all(query, (snapshot_date, snapshot_date))
        ]

    @_on_error(False, "배치 실행 기록 조회 실패")
    def has_batch_run(self, batch_name: str, execution_date: str) -> bool:
        """해당 일자에 배치(batch_name)가 실행·기록되었는지 여부 (예: closing_1600 일일 결산)"""
        rows = fetch_all(
            "SELECT COUNT(*) FROM batch_execution_logs WHERE batch_name = ? AND execution_date = ?",
            (batch_name, execution_date),
        )
        return bool(rows) and rows[0][0] > 0

    @_on_error(False, "감사 로그 적재 실패")
    def record_batch_audit_log(self, batch_name: str, message: str = "", status: str | None = None) -> bool:
        """스케줄러 실행 직후 현재 DB 적재 현황을 집계하여 감사 로그 테이블에 자동 기록

        - status: 호출부 판정값(core.health.judge_batch). 없으면 당일 적재 건수 기준 기존 규칙
        - closing_1600 은 당일 스냅샷이 없으면 FAILED 가 아닌 한 WARNING
        """
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

        p_rows = fetch_all(check_query, (today,))
        p_row = (
            dict(zip(["us_cnt", "fx_cnt", "fund_cnt", "kr_cnt", "crypto_cnt"], p_rows[0], strict=True))
            if p_rows
            else {}
        )
        s_rows = fetch_all(snapshot_query, (today,))
        s_row = {"cnt": s_rows[0][0]} if s_rows else {}

        us_cnt = int(p_row.get("us_cnt") or 0)
        fx_cnt = int(p_row.get("fx_cnt") or 0)
        fund_cnt = int(p_row.get("fund_cnt") or 0)
        kr_cnt = int(p_row.get("kr_cnt") or 0)
        crypto_cnt = int(p_row.get("crypto_cnt") or 0)
        snapshot_ok = 1 if int(s_row.get("cnt") or 0) > 0 else 0

        # 정상 여부 판별 상태값 도출
        if status is None:
            if batch_name == "closing_1600":
                status = "SUCCESS" if (kr_cnt > 0 and snapshot_ok == 1) else "WARNING"
            else:
                status = "INFO"
        if batch_name == "closing_1600" and not snapshot_ok and status != "FAILED":
            status = "WARNING"
            message = f"{message} | 당일 스냅샷 미생성"

        return execute(
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

    @_on_error(dict, "이상징후 기준값 조회 실패")
    def get_detector_settings(self) -> dict[str, float]:
        """이상징후 감시 기준값(detector_settings) 전체 조회. 실패 시 빈 dict 반환."""
        return {
            key: float(value)
            for key, value in fetch_all("SELECT setting_key, setting_value FROM detector_settings")
        }

    @_on_error(False, lambda key, *_args, **_kwargs: f"이상징후 기준값 저장 실패 [{key}]")
    def set_detector_setting(self, key: str, value: float, description: str | None = None) -> bool:
        """이상징후 감시 기준값 1건 UPSERT (다음 감시 주기부터 반영)."""
        return execute(
            """
            INSERT INTO detector_settings (setting_key, setting_value, description) VALUES (?, ?, ?)
            ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
            """,
            (key, value, description),
        )

    @_on_error(list, "알림 기록 조회 실패")
    def get_recent_anomaly_alerts(self, days: int = 7, limit: int = 15) -> list[dict]:
        """최근 days 일 이상징후·리밸런싱 알림 발송 기록 (최신순)."""
        rows = fetch_all(
            """
            SELECT alert_date, ticker_code, event_type, change_pct, alert_count, updated_at
            FROM anomaly_alert_logs
            WHERE alert_date > DATE_SUB(CURDATE(), INTERVAL ? DAY)
            ORDER BY updated_at DESC LIMIT ?
            """,
            (days, limit),
        )
        cols = ["alert_date", "ticker_code", "event_type", "change_pct", "alert_count", "updated_at"]
        return [dict(zip(cols, r, strict=True)) for r in rows]

    @_on_error(None, "이상징후 발송 이력 조회 실패")
    def get_last_anomaly_alert_pct(self, alert_date, ticker_code: str, event_type: str) -> float | None:
        """해당 일자·종목·이벤트로 마지막 발송한 등락률 조회 (발송 이력 없으면 None)"""
        query = """
            SELECT change_pct FROM anomaly_alert_logs
            WHERE alert_date = %s AND ticker_code = %s AND event_type = %s
        """
        rows = fetch_all(query, (alert_date, ticker_code, event_type))
        return float(rows[0][0]) if rows else None

    @_on_error(False, "이상징후 발송 기록 저장 실패")
    def save_anomaly_alert(self, alert_date, ticker_code: str, event_type: str, change_pct: float) -> bool:
        """이상징후 알림 발송 기록 UPSERT (같은 일자·종목·이벤트는 마지막 등락률로 갱신)"""
        query = """
            INSERT INTO anomaly_alert_logs (alert_date, ticker_code, event_type, change_pct)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                change_pct = VALUES(change_pct),
                alert_count = alert_count + 1
        """
        return execute(query, (alert_date, ticker_code, event_type, round(change_pct, 2)))

    def has_recent_anomaly_alert(self, ticker_code: str, event_type: str, days: int = 1) -> bool:
        """오늘 포함 최근 days 일 내 같은 대상·이벤트 알림 기록 여부 (days=1 이면 오늘). 연결·쿼리 실패 시 예외."""
        rows = fetch_all(
            """
            SELECT 1 FROM anomaly_alert_logs
            WHERE ticker_code = ? AND event_type = ? AND alert_date > DATE_SUB(CURDATE(), INTERVAL ? DAY)
            LIMIT 1
            """,
            (ticker_code, event_type, days),
            strict=True,
        )
        return bool(rows)

    def record_anomaly_alert(self, ticker_code: str, event_type: str, change_pct: float = 0.0) -> None:
        """오늘(DB CURDATE) 날짜로 알림 기록 UPSERT (재발송 시 change_pct 갱신·alert_count 증가). 쿼리 실패 시 예외."""
        execute(
            """
            INSERT INTO anomaly_alert_logs (alert_date, ticker_code, event_type, change_pct)
            VALUES (CURDATE(), ?, ?, ?)
            ON DUPLICATE KEY UPDATE change_pct = VALUES(change_pct), alert_count = alert_count + 1
            """,
            (ticker_code, event_type, round(change_pct, 2)),
        )

    def get_ticker_name_map(self) -> dict[str, str]:
        """{종목코드: 종목명} — 같은 종목코드의 가장 최근 거래(id 최대) 종목명으로 통일."""
        rows = fetch_all(
            """
            SELECT t.ticker_code, t.ticker_name FROM transactions t
            JOIN (SELECT ticker_code, MAX(id) AS id FROM transactions WHERE ticker_code IS NOT NULL GROUP BY ticker_code) m
                ON t.id = m.id
            """
        )
        return dict(rows)


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
