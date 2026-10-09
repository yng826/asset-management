"""펀드 기준가(NAV) 과거 이력 백필 스크립트 (금융투자협회 전자공시 dis.kofia.or.kr)

배경:
    펀드 시세는 펀드닥터 스크래핑(당일 기준가 1건)만 수집해 2026-09-04 이전 이력이 없음.
    초기잔고(2026-05-01) 펀드는 과거 매수원가로 평가되다가 09-04 실제 기준가 반영 시
    누적 평가이익이 하루에 몰려 일간 수익률·MDD·기여도 차트가 왜곡됨.

사용법 (DB 는 환경변수 DB_* 기준 — 개발 DB 에서 먼저 실행):
    python -m scripts.backfill_fund_nav 2026-04-24 2026-09-03             # dry-run: 조회 건수·샘플만 출력
    python -m scripts.backfill_fund_nav 2026-04-24 2026-09-03 --apply     # daily_prices UPSERT
    python -m scripts.backfill_snapshots 2026-05-01 2026-09-03            # 반영 후 스냅샷 재계산

대상: transactions 에 등장한 펀드 표준코드(K5…, KR5…) 전체.
"""

import sys
import xml.etree.ElementTree as ET

import requests

from core.fetcher.fund import is_fund_ticker
from database.connection import DBConnectionError, execute_many, fetch_all

KOFIA_URL = "https://dis.kofia.or.kr/proframeWeb/XMLSERVICES/"
KOFIA_BODY = """<?xml version="1.0" encoding="utf-8"?>
<message>
  <proframeHeader>
    <pfmAppName>FS-DIS2</pfmAppName>
    <pfmSvcName>DISFundStdPrcStutSO</pfmSvcName>
    <pfmFnName>select</pfmFnName>
  </proframeHeader>
  <systemHeader></systemHeader>
  <DISCondFuncDTO>
    <tmpV30>{start}</tmpV30>
    <tmpV31>{end}</tmpV31>
    <tmpV10>0</tmpV10>
    <tmpV12>{code}</tmpV12>
  </DISCondFuncDTO>
</message>"""


def fetch_kofia_nav_history(fund_code: str, start_date: str, end_date: str) -> list[tuple[str, float]]:
    """KOFIA 기준가 추이 조회 → [(YYYY-MM-DD, 기준가)] (오래된 순). 실패 시 빈 리스트.

    응답 필드: tmpV1 = 기준일(YYYYMMDD), tmpV2 = 기준가, tmpV12 = 펀드 표준코드
    """
    body = KOFIA_BODY.format(start=start_date.replace("-", ""), end=end_date.replace("-", ""), code=fund_code)
    try:
        resp = requests.post(
            KOFIA_URL,
            data=body.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        print(f"  ❌ {fund_code}: KOFIA 조회 실패 ({e})")
        return []

    rows = []
    for el in root.iter():
        day, nav, code = el.findtext("tmpV1"), el.findtext("tmpV2"), el.findtext("tmpV12")
        if not (day and nav and code == fund_code and len(day) == 8):
            continue
        try:
            rows.append((f"{day[:4]}-{day[4:6]}-{day[6:]}", float(nav.replace(",", ""))))
        except ValueError:
            continue
    return sorted(rows)


def get_fund_tickers() -> list[str]:
    try:
        rows = fetch_all(
            "SELECT DISTINCT ticker_code FROM transactions WHERE ticker_code IS NOT NULL", strict=True
        )
    except DBConnectionError:
        print("❌ DB 연결 실패")
        sys.exit(1)
    codes = [r[0].strip().upper() for r in rows]
    return sorted(c for c in codes if is_fund_ticker(c))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    if len(args) != 2:
        print(__doc__)
        sys.exit(1)
    start_date, end_date = args

    funds = get_fund_tickers()
    print(
        f"🚀 펀드 기준가 백필 ({start_date} ~ {end_date}) 대상 {len(funds)}개 {'[APPLY]' if apply else '[DRY-RUN]'}"
    )

    records = []
    for code in funds:
        rows = fetch_kofia_nav_history(code, start_date, end_date)
        if rows:
            print(
                f"  ✅ {code}: {len(rows)}일 ({rows[0][0]} {rows[0][1]:,.2f} ~ {rows[-1][0]} {rows[-1][1]:,.2f})"
            )
        else:
            print(f"  ⚠️ {code}: 기준가 없음 (스킵)")
        records.extend((day, code, nav) for day, nav in rows)

    if not apply:
        print(f"\n💡 dry-run: {len(records)}건 적재 예정. 반영하려면 --apply")
        return

    saved = execute_many(
        """
        INSERT INTO daily_prices (price_date, ticker_code, close_price)
        VALUES (?, ?, ?)
        ON DUPLICATE KEY UPDATE close_price = VALUES(close_price), updated_at = CURRENT_TIMESTAMP
        """,
        records,
    )
    if not saved:
        print("❌ DB 연결 실패")
        sys.exit(1)
    print(f"\n🎉 daily_prices {len(records)}건 UPSERT 완료")


if __name__ == "__main__":
    main()
