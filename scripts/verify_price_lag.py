import sys
from datetime import datetime

sys.path.append(".")

from database.connection import DBConnectionError, fetch_all


def verify_price_lag():
    # 1. 최근 5영업일 날짜 추출 (삼성전자 005930 기준 최근 5개 거래일)
    try:
        rows = fetch_all(
            """
            SELECT DISTINCT price_date
            FROM daily_prices
            WHERE ticker_code = '005930'
            ORDER BY price_date DESC
            LIMIT 5
            """,
            strict=True,
        )
    except DBConnectionError:
        print("❌ DB 연결 실패")
        return
    if not rows:
        print("⚠️ daily_prices에 삼성전자(005930) 데이터가 없습니다.")
        return

    business_dates = [r[0].strftime("%Y-%m-%d") if hasattr(r[0], "strftime") else str(r[0]) for r in rows]
    business_dates.sort()  # 오름차순 정렬

    print(f"📅 검증 대상 최근 영업일 (5일): {business_dates}\n")

    # 2. 대표 종목 설정 (실제 보유/수집 대상인 TSLA, AAPL, PFE 등 포함)
    targets = [
        {"class": "국내주식/ETF", "code": "005930", "name": "삼성전자"},
        {"class": "국내주식/ETF", "code": "379780", "name": "RISE 미국S&P500"},
        {"class": "해외주식/ETF", "code": "TSLA", "name": "테슬라"},
        {"class": "해외주식/ETF", "code": "AAPL", "name": "애플"},
        {"class": "해외주식/ETF", "code": "PFE", "name": "화이자"},
    ]

    # 공모 펀드 샘플 1개 동적 조회
    fund_rows = fetch_all(
        """
        SELECT DISTINCT ticker_code
        FROM daily_prices
        WHERE ticker_code REGEXP '^(KR5|K55)'
        LIMIT 1
        """
    )
    if fund_rows:
        targets.append({"class": "공모펀드", "code": fund_rows[0][0], "name": fund_rows[0][0]})

    print("=" * 95)
    print(
        f"{'영업일(T)':<12} | {'자산군':<12} | {'종목명(코드)':<22} | {'저장된 price_date':<18} | {'시차(Lag) 상태'}"
    )
    print("=" * 95)

    for b_date in business_dates:
        for t in targets:
            code = t["code"]
            name_code = f"{t['name']}({code})" if t["name"] != code else code
            # 해당 영업일(b_date)에 정확히 수집된 가격이 있는지, 혹은 이전 가격인지 확인
            found = fetch_all(
                """
                SELECT price_date, close_price
                FROM daily_prices
                WHERE ticker_code = ? AND price_date <= ?
                ORDER BY price_date DESC
                LIMIT 1
                """,
                (code, b_date),
            )
            res = found[0] if found else None

            if res:
                stored_date = res[0].strftime("%Y-%m-%d") if hasattr(res[0], "strftime") else str(res[0])
                dt_b = datetime.strptime(b_date, "%Y-%m-%d")
                dt_s = datetime.strptime(stored_date, "%Y-%m-%d")
                diff_days = (dt_b - dt_s).days

                if diff_days == 0:
                    status = "정합 (T일 일치)"
                elif diff_days == 1:
                    status = "1영업일 지연 (T-1)"
                elif diff_days > 1:
                    status = f"{diff_days}일 지연 (주말포함 등)"
                else:
                    status = "미래 일자 (주의)"
            else:
                stored_date = "데이터 없음"
                status = "수집 실패/누락"

            print(f"{b_date:<12} | {t['class']:<12} | {name_code:<22} | {stored_date:<18} | {status}")

    print("=" * 95)


if __name__ == "__main__":
    verify_price_lag()
