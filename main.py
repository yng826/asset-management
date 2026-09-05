from bot.bot import create_bot_app
from core.scheduler import setup_scheduler
import os
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

def main():
    print("🚀 자산관리 AI 봇 가동 시작...")
    
    # 환경 변수에서 챗 ID 가져오기
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    if not TELEGRAM_CHAT_ID:
        print("🚨 환경 변수 TELEGRAM_CHAT_ID가 설정되지 않았습니다. .env 파일을 확인해주세요.")
        print("   스케줄러 기능이 비활성화됩니다.")
        # 스케줄러 없이 봇만 실행하려면 이 줄을 제거하거나 아래 로직 수정
        # return # 스케줄러가 필수라면 이 줄 활성화

    app = create_bot_app()

    # 스케줄러 설정 및 시작
    if TELEGRAM_CHAT_ID:
        scheduler = setup_scheduler(app, TELEGRAM_CHAT_ID)
        scheduler.start()
        print("✅ APScheduler가 시작되었습니다.")

        # 봇 종료 시 스케줄러도 종료되도록 콜백 등록
        # ApplicationBuilder().build() 대신 app.add_shutdown_handler 사용
        app.add_shutdown_handler(scheduler.shutdown)

    app.run_polling()


if __name__ == "__main__":
    main()

