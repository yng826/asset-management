import sys
from datetime import datetime, timedelta

sys.path.append(".")

from core.calculator import save_snapshot_for_date


def backfill_snapshots(start_date: str, end_date: str):
    curr = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    print(f"🔄 {start_date} ~ {end_date} 스냅샷 백필 시작...")

    while curr <= end:
        d_str = curr.strftime("%Y-%m-%d")
        success = save_snapshot_for_date(d_str)
        if success:
            print(f"✅ {d_str} 스냅샷 생성 완료")
        else:
            print(f"⚠️ {d_str} 스냅샷 생성 실패 또는 데이터 없음")
        curr += timedelta(days=1)


if __name__ == "__main__":
    # 실행 시 인자를 주면 해당 날짜로, 없으면 2026-05-01부터 어제까지 실행
    s_date = sys.argv[1] if len(sys.argv) > 1 else "2026-05-01"
    e_date = sys.argv[2] if len(sys.argv) > 2 else (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    backfill_snapshots(s_date, e_date)
