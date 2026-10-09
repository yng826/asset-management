"""미래에셋 일반 AAPL 초기잔고 통화 보정용 1회성 마이그레이션 스크립트 (2026-10-09)

배경:
    migrate_ledger_cleanup 4단계(해외주식 currency KRW → USD)가 '토스 일반'만 대상으로 해서
    미래에셋 일반 AAPL 초기잔고(id 39, 2.0039주 × $134.20 = $268.93)가 KRW 로 남음.
    같은 날 "초기 현금 보정" DEPOSIT(id 101, 7,344,769.06원)은 삼성전자 7,344,500.13원 + AAPL 268.93 을
    모두 원화로 보고 합산 상쇄한 값이라, 통화만 바꾸면 KRW 현금 +268.93원 / USD 현금 -$268.93 이 됨.

사용법 (DB 는 환경변수 DB_* 기준 — 개발 DB 에서 먼저 실행):
    python -m scripts.migrate_aapl_currency            # dry-run: 변경 예정 내역만 출력
    python -m scripts.migrate_aapl_currency --apply    # 실제 반영 (단일 트랜잭션, 실패 시 전체 롤백)
    python -m scripts.backfill_snapshots 2026-05-01    # 반영 후 스냅샷 재계산 (어제까지)

처리 내용 (재실행해도 결과가 같도록 조건을 걸어 둠):
    1) id 39 AAPL BUY currency KRW → USD
    2) id 101 초기 현금 보정 KRW 7,344,769.06 → 7,344,500.13 (AAPL 몫 268.93 제외)
    3) 미래에셋 일반 2026-05-01 초기 현금 보정 USD 268.93 DEPOSIT 입력
    → 반영 후 계좌 현금: KRW 변화 없음, USD 0
"""

import sys

from database.connection import get_connection

CORRECTION_MEMO = "초기 현금 보정 (2026-09-01 이전 초기 보유분 매입액 상쇄)"

STEPS = [
    (
        "1) id 39 AAPL BUY currency KRW → USD",
        "UPDATE transactions SET currency = 'USD' WHERE id = 39 AND ticker_code = 'AAPL' AND currency = 'KRW'",
    ),
    (
        "2) id 101 초기 현금 보정 KRW 7,344,769.06 → 7,344,500.13",
        "UPDATE transactions SET total_amount = 7344500.13 "
        "WHERE id = 101 AND account_name = '미래에셋 일반' AND currency = 'KRW' AND total_amount = 7344769.06",
    ),
    (
        "3) 미래에셋 일반 초기 현금 보정 USD 268.93 DEPOSIT 입력",
        "INSERT INTO transactions (trans_date, account_name, ticker_name, ticker_code, action_type, "
        "quantity, unit_price, total_amount, currency, memo) "
        "SELECT '2026-05-01', '미래에셋 일반', 'USD_CASH', 'CASH_USD', 'DEPOSIT', 0, 0, 268.93, 'USD', ? "
        "FROM DUAL WHERE NOT EXISTS ("
        "  SELECT 1 FROM transactions WHERE account_name = '미래에셋 일반' AND trans_date = '2026-05-01' "
        "  AND action_type = 'DEPOSIT' AND currency = 'USD' AND memo = ?)",
    ),
]


def print_cash(cur) -> None:
    cur.execute(
        """
        SELECT currency, SUM(CASE WHEN action_type IN ('DEPOSIT', 'SELL', 'DIVIDEND') THEN total_amount
                                  WHEN action_type IN ('BUY', 'WITHDRAW') THEN -total_amount ELSE 0 END)
        FROM transactions WHERE account_name = '미래에셋 일반' GROUP BY currency
        """
    )
    for currency, net in cur.fetchall():
        print(f"   미래에셋 일반 현금 {currency}: {float(net):,.2f}")


def main():
    apply = "--apply" in sys.argv
    conn = get_connection()
    if not conn:
        print("❌ DB 연결 실패")
        sys.exit(1)

    cur = conn.cursor()
    cur.execute("SELECT DATABASE(), CURRENT_USER()")
    db_name, db_user = cur.fetchone()
    print(f"🗄️  대상 DB: {db_name} (사용자 {db_user}) — {'APPLY' if apply else 'DRY-RUN'}")

    cur.execute("SELECT id, ticker_code, total_amount, currency FROM transactions WHERE id IN (39, 101)")
    for row in cur.fetchall():
        print(f"   현재 id {row[0]}: {row[1]} {float(row[2]):,.2f} {row[3]}")
    print_cash(cur)

    if not apply:
        print("ℹ️  dry-run 입니다. 반영하려면 --apply")
        conn.close()
        return

    try:
        conn.autocommit = False
        for i, (desc, sql) in enumerate(STEPS):
            cur.execute(sql, (CORRECTION_MEMO, CORRECTION_MEMO) if i == 2 else ())
            print(f"{desc}: {cur.rowcount}건 반영")
        conn.commit()
        print("✅ 커밋 완료")
        print_cash(cur)
        print("👉 다음: python -m scripts.backfill_snapshots 2026-05-01")
    except Exception as e:
        conn.rollback()
        print(f"❌ 오류로 전체 롤백: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
