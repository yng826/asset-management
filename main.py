import asyncio
import logging
import os
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv
from telegram.error import NetworkError

from bot.bot import create_bot_app
from config.settings import ENV_FILE_PATH
from core.scheduler import setup_scheduler

# env 파일 로드 (기본 .env, 개발은 ENV_FILE=.env.dev — config/settings.py 참고)
load_dotenv(ENV_FILE_PATH)


LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    log_file = "logs/app.log"

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    logger = logging.getLogger()
    # ⚠️ [핵심] 이미 핸들러가 등록되어 있다면 다시 추가하지 않고 즉시 리턴
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    logger.handlers.clear()  # 기존 핸들러 초기화

    # 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 파일 핸들러: 5MB 마다 회전, app.log.1 ~ app.log.5 보관 (최대 약 30MB)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 외부 통신 노이즈 차단
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Updater").addFilter(_downgrade_polling_network_error)


def _downgrade_polling_network_error(record: logging.LogRecord) -> bool:
    """폴링 중 일시적 네트워크 오류(NetworkError·TimedOut)는 라이브러리가 자동 재시도하므로
    traceback 없는 WARNING 한 줄로 낮춤 (헬스체크 에러 집계 제외). Conflict 등은 ERROR 유지."""
    exc = record.exc_info[1] if record.exc_info else None
    if isinstance(exc, NetworkError):
        record.levelno, record.levelname = logging.WARNING, "WARNING"
        record.msg = f"{record.msg} ({type(exc).__name__}: {exc})"
        record.args = None
        record.exc_info = record.exc_text = None
    return True


async def run_bot():
    """
    봇과 스케줄러의 라이프사이클을 관리하며 봇을 실행합니다.
    """
    setup_logging()
    app = create_bot_app()
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    scheduler = None
    if chat_id:
        # 봇 초기화 시점에 스케줄러 생성 및 시작
        # setup_scheduler 내부에서 이미 AsyncIOScheduler를 사용하여
        # 현재 실행 중인 이벤트 루프를 자동으로 감지합니다.
        scheduler = setup_scheduler(app, chat_id)
        scheduler.start()
        logging.info("✅ APScheduler가 시작되었습니다.")
    else:
        logging.warning("🚨 TELEGRAM_CHAT_ID가 설정되지 않았습니다. 스케줄러가 비활성화됩니다.")

    try:
        # 봇 실행
        await app.initialize()
        # post_init은 run_polling()에서만 자동 호출되므로 수동 기동 시 직접 호출 (명령어 목록 동기화)
        if app.post_init:
            await app.post_init(app)
        await app.start()
        await app.updater.start_polling()

        # 봇이 종료 시그널(Ctrl+C 등)을 받을 때까지 대기
        stop_event = asyncio.Event()
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logging.info("🛑 봇 종료 신호가 감지되었습니다.")
    finally:
        # 종료 시 스케줄러 및 봇 리소스 정리
        if scheduler:
            scheduler.shutdown(wait=False)
            logging.info("✅ APScheduler가 종료되었습니다.")

        # 봇 종료
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        logging.info("✅ 봇이 완전히 종료되었습니다.")


def main():
    print("🚀 자산관리 AI 봇 가동 시작...")
    try:
        asyncio.run(run_bot())
    except Exception as e:
        print(f"🚨 봇 실행 중 치명적 오류 발생: {e}")


if __name__ == "__main__":
    main()
