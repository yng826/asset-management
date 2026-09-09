"""scripts/sync_commands.py - 텔레그램 명령어 메뉴 동기화 스크립트

사용법:
  python scripts/sync_commands.py
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from telegram import Bot, BotCommand

# 프로젝트 루트 경로 확보
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.commands import BOT_COMMANDS  # noqa: E402


async def sync():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        print("❌ TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    bot = Bot(token=token)

    # BOT_COMMANDS가 튜플 형태 [("status", "설명"), ...] 인 경우 BotCommand 객체로 변환
    tg_commands = []
    for item in BOT_COMMANDS:
        if isinstance(item, BotCommand):
            tg_commands.append(item)
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            tg_commands.append(BotCommand(item[0], item[1]))

    print("🔄 텔레그램 명령어 목록 동기화 중...")
    await bot.set_my_commands(tg_commands)

    print("✅ 동기화 완료! 등록된 명령어:")
    for cmd in tg_commands:
        print(f"  /{cmd.command:<10} - {cmd.description}")


if __name__ == "__main__":
    asyncio.run(sync())