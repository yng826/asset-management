#!/usr/bin/env bash
# scripts/dev.sh
# 개발용 compose 를 짧은 명령으로 조작하기 위한 헬퍼
#
# 사용법:
#   ./scripts/dev.sh up       # 빌드 + 실행 (백그라운드)
#   ./scripts/dev.sh logs     # 로그 스트림
#   ./scripts/dev.sh restart  # 봇 컨테이너 재시작 (수동 재시작이 필요할 때)
#   ./scripts/dev.sh down     # 컨테이너 종료
#   ./scripts/dev.sh rebuild  # 이미지 재빌드 (의존성 변경 시)
#   ./scripts/dev.sh shell    # 컨테이너 내부 셀로 진입
#   ./scripts/dev.sh status   # 컨테이너 상태 확인

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

CMD="${1:-up}"
COMPOSE="docker compose -f docker-compose.dev.yml"

case "$CMD" in
  up)
    $COMPOSE up -d --build
    echo "▶️  로그를 보려면: $0 logs"
    ;;
  logs)
    $COMPOSE logs -f bot-dev
    ;;
  restart)
    $COMPOSE restart bot-dev
    ;;
  down)
    $COMPOSE down
    ;;
  rebuild)
    $COMPOSE build --no-cache
    ;;
  shell)
    $COMPOSE exec bot-dev bash
    ;;
  status)
    $COMPOSE ps
    docker inspect asset-manager-bot-dev --format '{{.State.Status}} | {{.State.Health.Status}}' 2>/dev/null || true
    ;;
  *)
    echo "사용법: $0 {up|logs|restart|down|rebuild|shell|status}" >&2
    exit 1
    ;;
esac
