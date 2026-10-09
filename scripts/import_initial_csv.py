import csv
import os

from database.connection import execute_many

CSV_FILE_PATH = "initial_holdings.csv"


def import_initial_holdings():
    if not os.path.exists(CSV_FILE_PATH):
        print(f"❌ 파일을 찾을 수 없습니다: {CSV_FILE_PATH}")
        return

    insert_sql = """
        INSERT INTO transactions (
            trans_date,
            account_name,
            ticker_name,
            ticker_code,
            action_type,
            quantity,
            unit_price,
            total_amount,
            memo
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    rows_to_insert = []
    with open(CSV_FILE_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qty = float(row["quantity"])
            unit_price = float(row["unit_price"])
            total_amt = float(row.get("total_amount") or (qty * unit_price))

            rows_to_insert.append(
                (
                    row["trans_date"],
                    row["account_name"],
                    row["ticker_name"],
                    row["ticker_code"],
                    "BUY",
                    qty,
                    unit_price,
                    total_amt,
                    row.get("memo", "초기잔고등록"),
                )
            )

    try:
        if not execute_many(insert_sql, rows_to_insert):
            print("❌ 데이터베이스 연결 실패")
            return
        print(f"✅ 총 {len(rows_to_insert)}건의 초기 잔고가 transactions 테이블에 성공적으로 등록되었습니다.")
    except Exception as e:
        print(f"❌ 임포트 실패: {e}")


if __name__ == "__main__":
    import_initial_holdings()
