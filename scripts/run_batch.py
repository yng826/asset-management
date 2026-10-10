"""scripts/run_batch.py - 정기 배치 수동 1회 실행 (재시작으로 놓친 배치 보충 등)

스케줄러와 같은 함수를 호출하므로 텔레그램 발송·감사 로그 기록까지 동일하게 수행된다.

사용법:
  python -m scripts.run_batch weekend       # 주말 결산 (토·일 09:05)
  python -m scripts.run_batch --list        # 실행 가능한 배치 목록
  ENV_FILE=.env.dev python -m scripts.run_batch closing   # 개발 환경
"""

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from telegram.ext import Application

# 프로젝트 루트 경로 확보
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config.settings import ENV_FILE_PATH  # noqa: E402


def _jobs() -> dict:
    """배치 이름 → (설명, async 함수(application, chat_id)). env 로드 후 import."""
    from bot.weekly_report import send_weekly_report
    from core.health import check_health_and_alert
    from core.rebalance import check_rebalance_drift
    from core.scheduler import daily_closing_report, morning_briefing, weekend_closing_report

    async def weekly(application, chat_id):
        await send_weekly_report(application.bot, chat_id)

    return {
        "morning": ("오전 브리핑 (평일 09:05)", morning_briefing),
        "closing": ("평일 결산 (16:00)", daily_closing_report),
        "weekend": ("주말 결산 (토·일 09:05)", weekend_closing_report),
        "weekly": ("주간 성과 리포트 (일 08:00)", weekly),
        "rebalance": ("목표 비중 이탈 알림 (16:10)", check_rebalance_drift),
        "health": ("봇 상태 점검 (16:30, 문제 있을 때만 알림)", check_health_and_alert),
    }


async def run(name: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("❌ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    label, func = _jobs()[name]
    print(f"▶️  {label} 실행 ({ENV_FILE_PATH})")
    async with Application.builder().token(token).build() as application:
        await func(application, chat_id)
    print("✅ 완료")


def main() -> None:
    load_dotenv(ENV_FILE_PATH)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    jobs = _jobs()
    parser = argparse.ArgumentParser(description="정기 배치 수동 1회 실행")
    parser.add_argument("name", nargs="?", choices=list(jobs), help="실행할 배치")
    parser.add_argument("--list", action="store_true", help="실행 가능한 배치 목록")
    args = parser.parse_args()

    if args.list or not args.name:
        for key, (label, _) in jobs.items():
            print(f"  {key:<10} {label}")
        return
    asyncio.run(run(args.name))


if __name__ == "__main__":
    main()
