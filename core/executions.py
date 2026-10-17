"""증권사 체결 대기(broker_executions) 승인 로직 (3단계 봇 쪽).

수집기가 PENDING 으로 쌓은 체결을 봇이 버튼으로 승인하면 transactions 원장에 기록한다.
- 상태 전이는 `UPDATE … WHERE status IN (…)` 한 문장으로 선점해 버튼을 두 번 눌러도 이중 기록되지 않는다.
- 총액은 수동 입력과 같이 수량 × 단가 (수수료·세금 제외).
"""

import logging

from database.connection import fetch_all, get_connection
from database.repository import AssetRepository

logger = logging.getLogger(__name__)

COLUMNS = "id, broker, account_name, order_id, trade_date, ticker_code, side, quantity, unit_price, currency, status"
_KEYS = COLUMNS.replace(" ", "").split(",")
QTY_EPS = 0.0001


def calc_total(quantity: float, unit_price: float, currency: str) -> float:
    """원장 총액: 수량 × 단가, 달러는 소수 2자리 / 원화는 정수 반올림 (거래 입력 화면과 동일).

    >>> calc_total(95, 31990, "KRW")
    3039050
    >>> calc_total(3, 12.345, "USD")
    37.04
    """
    total = quantity * unit_price
    return round(total, 2) if currency == "USD" else round(total)


def _row(r) -> dict:
    d = dict(zip(_KEYS, r, strict=True))
    d["quantity"] = float(d["quantity"])
    d["unit_price"] = float(d["unit_price"])
    return d


def get_execution(exec_id: int) -> dict | None:
    rows = fetch_all(f"SELECT {COLUMNS} FROM broker_executions WHERE id = ?", (exec_id,))
    return _row(rows[0]) if rows else None


def list_executions(status: str, only_unnotified: bool = False, limit: int = 50) -> list[dict]:
    query = f"SELECT {COLUMNS} FROM broker_executions WHERE status = ?"
    if only_unnotified:
        query += " AND notified_at IS NULL"
    query += " ORDER BY trade_date, id LIMIT ?"
    return [_row(r) for r in fetch_all(query, (status, limit))]


def _update(query: str, params: tuple) -> int:
    """UPDATE 실행 후 영향 행 수 (연결 실패 시 0). 쿼리 오류는 그대로 전파."""
    conn = get_connection()
    if not conn:
        return 0
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        count = cur.rowcount
        cur.close()
        return count
    finally:
        conn.close()


def mark_notified(exec_id: int) -> None:
    _update("UPDATE broker_executions SET notified_at = NOW() WHERE id = ?", (exec_id,))


def is_in_ledger(e: dict) -> bool:
    """같은 날짜·계좌·종목·방향·수량이 원장에 이미 있으면 True (D5: 수동으로 입력한 거래)."""
    rows = fetch_all(
        """
        SELECT COUNT(*) FROM transactions
        WHERE trans_date = ? AND account_name = ? AND ticker_code = ? AND action_type = ?
          AND ABS(quantity - ?) < ?
        """,
        (e["trade_date"], e["account_name"], e["ticker_code"], e["side"], e["quantity"], QTY_EPS),
    )
    return bool(rows and rows[0][0])


def classify_pending() -> list[dict]:
    """알리지 않은 PENDING 중 원장에 이미 있는 건을 DUPLICATE 로 바꾸고 그 목록을 반환."""
    duplicates = []
    for e in list_executions("PENDING", only_unnotified=True):
        if is_in_ledger(e) and _update(
            "UPDATE broker_executions SET status = 'DUPLICATE' WHERE id = ? AND status = 'PENDING'",
            (e["id"],),
        ):
            duplicates.append({**e, "status": "DUPLICATE"})
    return duplicates


def ticker_name(code: str) -> str:
    """원장의 종목명 → ticker_master → 종목코드 순으로 찾는다."""
    name = AssetRepository().get_ticker_name_map().get(code)
    if name:
        return name
    rows = fetch_all("SELECT ticker_name FROM ticker_master WHERE ticker_code = ?", (code,))
    return rows[0][0] if rows else code


def approve(exec_id: int) -> tuple[str, dict | None]:
    """체결 1건 승인 → 원장 기록. 반환 (결과, 체결) 결과는 'ok' | 'already' | 'missing' | 'failed'.

    PENDING·DUPLICATE 만 승인할 수 있다 ("그래도 기록"이 DUPLICATE 를 승인).
    """
    e = get_execution(exec_id)
    if e is None:
        return "missing", None
    claimed = _update(
        "UPDATE broker_executions SET status = 'APPROVED', decided_at = NOW() "
        "WHERE id = ? AND status IN ('PENDING', 'DUPLICATE')",
        (exec_id,),
    )
    if not claimed:
        return "already", get_execution(exec_id)

    memo = f"[증권사 체결 #{exec_id}] {e['broker']} {e['order_id']}"
    ok = AssetRepository().add_transaction(
        {
            "trans_date": e["trade_date"],
            "account_name": e["account_name"],
            "ticker_name": ticker_name(e["ticker_code"]),
            "ticker_code": e["ticker_code"],
            "action_type": e["side"],
            "quantity": e["quantity"],
            "unit_price": e["unit_price"],
            "total_amount": calc_total(e["quantity"], e["unit_price"], e["currency"]),
            "currency": e["currency"],
        },
        memo,
    )
    if not ok:
        # 원장 기록 실패: 원래 상태로 되돌려 다시 시도할 수 있게 한다
        _update(
            "UPDATE broker_executions SET status = ?, decided_at = NULL WHERE id = ? AND status = 'APPROVED'",
            (e["status"], exec_id),
        )
        logger.error(f"❌ 체결 승인 원장 기록 실패: {e}")
        return "failed", e
    rows = fetch_all("SELECT MAX(id) FROM transactions WHERE memo = ?", (memo,))
    if rows and rows[0][0]:
        _update("UPDATE broker_executions SET transaction_id = ? WHERE id = ?", (rows[0][0], exec_id))
    logger.info(f"✅ 체결 승인 → 원장 기록: {memo}")
    return "ok", {**e, "status": "APPROVED"}


def reject(exec_id: int) -> tuple[str, dict | None]:
    """체결 무시: PENDING·DUPLICATE → REJECTED (행은 보관해 수집기가 되살리지 않는다)."""
    e = get_execution(exec_id)
    if e is None:
        return "missing", None
    changed = _update(
        "UPDATE broker_executions SET status = 'REJECTED', decided_at = NOW() "
        "WHERE id = ? AND status IN ('PENDING', 'DUPLICATE')",
        (exec_id,),
    )
    return ("ok", {**e, "status": "REJECTED"}) if changed else ("already", get_execution(exec_id))
