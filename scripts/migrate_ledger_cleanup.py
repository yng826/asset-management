"""거래 원장 정합성 정리 + 현금 계산 정식 장부(B안) 전환용 1회성 마이그레이션 스크립트 (2026-10-09)

사용법 (DB 는 환경변수 DB_* 기준 — 개발 DB 에서 먼저 실행):
    python -m scripts.migrate_ledger_cleanup            # dry-run: 변경 예정 내역만 출력
    python -m scripts.migrate_ledger_cleanup --apply    # 실제 반영 (단일 트랜잭션, 실패 시 전체 롤백)

처리 내용:
    0) transactions 백업 테이블 생성 (transactions_backup_20261009, 이미 있으면 건너뜀)
    1) 토스 거래 오배정 수정: id 80, 82, 83, 84 (한투일반계좌 → 토스 일반)
    2) 계좌명 통합: 이표기 → 정식 계좌명
    3) 종목코드 수정: TIGER 미국배당다우존스 371450 → 458730 / RISE 미국S&P500 458730 → 379780
    4) 토스 일반 해외주식·USD_CASH 의 currency KRW → USD
    5) 단가 오기 수정: id 73, 88, 90, 92 (단가 = 총액 ÷ 수량)
    6) 초기 현금 행(CASH 티커 BUY) → DEPOSIT 변환
    7) 초기 보유분 매입액 상쇄용 "초기 현금 보정" DEPOSIT 입력
       (2026-09-01 이전 BUY 를 계좌·통화·일자별로 합산, 기존 "9월 1일 이전 BUY 무시" 규칙과 동일한 잔액 보장)

모든 단계는 재실행해도 결과가 같도록(멱등) 조건을 걸어 둠.
"""

import sys

from database.connection import get_connection

BACKUP_TABLE = "transactions_backup_20261009"
CORRECTION_MEMO = "초기 현금 보정 (2026-09-01 이전 초기 보유분 매입액 상쇄)"
LEGACY_CUTOFF = "2026-09-01"

ACCOUNT_RENAMES = {
    "토스증권기본계좌": "토스 일반",
    "한투일반계좌": "한투 일반",
    "한투연금저축": "한투 연금저축",
    "카카오페이연금저축계좌": "카카오 연금저축",
}

# (설명, SQL, 파라미터) — dry-run 에서는 같은 WHERE 조건으로 대상 건수만 조회
STEPS = [
    (
        "1) 토스 거래 오배정 수정 (id 80, 82, 83, 84 → 토스 일반)",
        "UPDATE transactions SET account_name = '토스 일반' WHERE id IN (80, 82, 83, 84) AND account_name = '한투일반계좌'",
        (),
    ),
    *[
        (
            f"2) 계좌명 통합: {old} → {new}",
            "UPDATE transactions SET account_name = ? WHERE account_name = ?",
            (new, old),
        )
        for old, new in ACCOUNT_RENAMES.items()
    ],
    (
        "3) 종목코드 수정: TIGER 미국배당다우존스 371450 → 458730",
        "UPDATE transactions SET ticker_code = '458730' WHERE ticker_name = 'TIGER 미국배당다우존스' AND ticker_code = '371450'",
        (),
    ),
    (
        "3) 종목코드 수정: RISE 미국S&P500 458730 → 379780",
        "UPDATE transactions SET ticker_code = '379780' WHERE ticker_name = 'RISE 미국S&P500' AND ticker_code = '458730'",
        (),
    ),
    (
        "4) 토스 일반 해외주식·USD_CASH currency KRW → USD",
        "UPDATE transactions SET currency = 'USD' WHERE account_name = '토스 일반' "
        "AND ticker_code IN ('GOOGL', 'TSLA', 'PFE', 'QQQ', 'CASH_USD') AND currency = 'KRW'",
        (),
    ),
    (
        "5) 단가 오기 수정 (id 73, 88, 90, 92: 단가 = 총액 ÷ 수량)",
        "UPDATE transactions SET unit_price = ROUND(total_amount / quantity, 2) "
        "WHERE id IN (73, 88, 90, 92) AND quantity > 0 AND ABS(quantity * unit_price - total_amount) > 1",
        (),
    ),
    (
        "6) 초기 현금 행(CASH 티커 BUY) → DEPOSIT 변환",
        "UPDATE transactions SET action_type = 'DEPOSIT', quantity = 0, unit_price = 0 "
        "WHERE action_type = 'BUY' AND ticker_code IN ('CASH_KRW', 'CASH_USD')",
        (),
    ),
]

CORRECTION_QUERY = f"""
    SELECT account_name, COALESCE(currency, 'KRW') AS currency, trans_date, SUM(total_amount) AS buy_amount
    FROM transactions
    WHERE action_type = 'BUY' AND trans_date < '{LEGACY_CUTOFF}'
      AND ticker_code NOT IN ('CASH_KRW', 'CASH_USD')
    GROUP BY account_name, COALESCE(currency, 'KRW'), trans_date
    ORDER BY trans_date, account_name
"""


def count_targets(cur, sql: str, params: tuple) -> int:
    """UPDATE 문의 WHERE 조건으로 대상 건수 조회"""
    where = sql.split(" WHERE ", 1)[1]
    cur.execute(
        f"SELECT COUNT(*) FROM transactions WHERE {where}", params[1:] if len(params) == 2 else params
    )
    return cur.fetchone()[0]


def main():
    apply = "--apply" in sys.argv
    conn = get_connection()
    if not conn:
        sys.exit(1)
    cur = conn.cursor()
    cur.execute("SELECT DATABASE(), CURRENT_USER()")
    db_name, db_user = cur.fetchone()
    print(f"🗄️  대상 DB: {db_name} (사용자 {db_user}) — {'APPLY' if apply else 'DRY-RUN'}")

    if apply:
        cur.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP_TABLE} AS SELECT * FROM transactions")
        cur.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}")
        print(f"0) 백업 테이블 {BACKUP_TABLE}: {cur.fetchone()[0]}건")
        conn.autocommit = False

    try:
        for desc, sql, params in STEPS:
            if apply:
                cur.execute(sql, params)
                print(f"{desc}: {cur.rowcount}건 반영")
            else:
                print(f"{desc}: 대상 {count_targets(cur, sql, params)}건")

        cur.execute("SELECT COUNT(*) FROM transactions WHERE memo = ?", (CORRECTION_MEMO,))
        if cur.fetchone()[0] > 0:
            print("7) 초기 현금 보정 DEPOSIT: 이미 입력됨 — 건너뜀")
        else:
            # dry-run 에서는 1~6단계가 반영되기 전 상태라 계좌명·통화가 정리 전 기준으로 표시됨
            cur.execute(CORRECTION_QUERY)
            rows = cur.fetchall()
            print(
                f"7) 초기 현금 보정 DEPOSIT {len(rows)}건{' 입력' if apply else ' 예정 (정리 전 계좌명 기준)'}:"
            )
            for account, currency, trans_date, amount in rows:
                print(f"   - {trans_date} {account} {currency} {float(amount):,.2f}")
                if apply:
                    cash_name, cash_code = (
                        ("USD_CASH", "CASH_USD") if currency == "USD" else ("KRW_CASH", "CASH_KRW")
                    )
                    cur.execute(
                        "INSERT INTO transactions (trans_date, account_name, ticker_name, ticker_code, action_type, "
                        "quantity, unit_price, total_amount, currency, memo) "
                        "VALUES (?, ?, ?, ?, 'DEPOSIT', 0, 0, ?, ?, ?)",
                        (trans_date, account, cash_name, cash_code, amount, currency, CORRECTION_MEMO),
                    )

        if apply:
            conn.commit()
            print("✅ 커밋 완료")
    except Exception as e:
        if apply:
            conn.rollback()
            print(f"❌ 오류로 전체 롤백: {e}")
        else:
            print(f"❌ 오류: {e}")
        sys.exit(1)
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
