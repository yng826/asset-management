from bot.bot import create_bot_app


def main():
    print("🚀 자산관리 AI 봇 가동 시작...")
    app = create_bot_app()
    app.run_polling()


if __name__ == "__main__":
    main()
