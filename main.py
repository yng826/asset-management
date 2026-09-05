import os
import asyncio
from dotenv import load_dotenv

from bot.bot import create_bot_app
from core.scheduler import setup_scheduler

# .env 파일 로드
load_dotenv()

async def run_bot():
    """
    봇과 스케줄러의 라이프사이클을 관리하며 봇을 실행합니다.
    """
    app = create_bot_app()
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    scheduler = None
    if chat_id:
        # 봇 초기화 시점에 스케줄러 생성 및 시작
        # setup_scheduler 내부에서 이미 AsyncIOScheduler를 사용하여 
        # 현재 실행 중인 이벤트 루프를 자동으로 감지합니다.
        scheduler = setup_scheduler(app, chat_id)
        scheduler.start()
        print("✅ APScheduler가 시작되었습니다.")
    else:
        print("🚨 TELEGRAM_CHAT_ID가 설정되지 않았습니다. 스케줄러가 비활성화됩니다.")

    try:
        # 봇 실행
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        
        # 봇이 종료 시그널(Ctrl+C 등)을 받을 때까지 대기
        stop_event = asyncio.Event()
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        print("🛑 봇 종료 신호가 감지되었습니다.")
    finally:
        # 종료 시 스케줄러 및 봇 리소스 정리
        if scheduler:
            scheduler.shutdown(wait=False)
            print("✅ APScheduler가 종료되었습니다.")
        
        # 봇 종료
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        print("✅ 봇이 완전히 종료되었습니다.")

def main():
    print("🚀 자산관리 AI 봇 가동 시작...")
    try:
        asyncio.run(run_bot())
    except Exception as e:
        print(f"🚨 봇 실행 중 치명적 오류 발생: {e}")

if __name__ == "__main__":
    main()

