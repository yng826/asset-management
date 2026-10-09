"""bot/commands.py - 텔레그램 명령어 목록 및 설명 정의"""

# (명령어, 설명) — 명령어 팝업에는 자주 쓰는 것만 노출
# /details·/chart·/history·/breakdown·/log·/target·/alert 는 핸들러를 유지하고 /start 전체 메뉴 버튼(또는 직접 입력)으로 접근
BOT_COMMANDS = [
    ("start", "👋 메뉴·하단 버튼 열기"),
    ("status", "📊 전체 자산 요약 및 당일 손익"),
    ("live", "⚡ 실시간 체결가 및 등락 현황"),
    ("pnl", "📈 최근 일자별 손익 내역"),
    ("trade", "✍️ 버튼으로 거래 입력"),
    ("weekly", "🗓 주간 성과 리포트"),
]
