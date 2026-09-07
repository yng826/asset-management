#!/bin/bash
# ==============================================================================
# 운영 환경 관리 스크립트 (Production Management Helper)
# ==============================================================================

CONTAINER_NAME="asset-manager-bot"

case "$1" in
  # 1. 운영 컨테이너 실시간 로그 확인
  logs)
    docker logs -f "$CONTAINER_NAME"
    ;;

  # 2. 호스트에 마운트된 파일 로그 확인
  app-logs)
    tail -f logs/app.log
    ;;

  # 3. 운영 컨테이너 셸 진입
  shell)
    docker exec -it "$CONTAINER_NAME" bash
    ;;

  # 4. 컨테이너 단순 재기동
  restart)
    echo "🔄 $CONTAINER_NAME 재기동 중..."
    docker restart "$CONTAINER_NAME"
    ;;

  # 5. 최신 이미지 강제 당겨오기 및 컨테이너 무중단 교체 (배포 갱신)
  update)
    echo "📦 GHCR 최신 이미지 다운로드 및 컨테이너 재생성..."
    docker compose pull
    docker compose up -d --force-recreate bot
    docker image prune -f
    ;;

  # 6. 운영 컨테이너 내부에서 원하는 시세 즉시 수집
  #    예: ./scripts/prod.sh fetch kr / ./scripts/prod.sh fetch crypto
  fetch)
    TARGET=${2:-all}
    echo "🚀 운영 환경에서 시세 수집 실행 [대상: $TARGET]..."
    docker exec -it "$CONTAINER_NAME" python scripts/fetch_price.py "$TARGET"
    ;;

  # 7. 상태 확인
  status)
    docker ps -f name="$CONTAINER_NAME"
    ;;

  *)
    echo "============================================================"
    echo " 사용법: $0 {logs|app-logs|shell|restart|update|fetch|status}"
    echo "============================================================"
    echo "  logs      : 도커 표준출력 로그 실시간 스트리밍"
    echo "  app-logs  : logs/app.log 파일 실시간 확인"
    echo "  shell     : 운영 컨테이너 bash 셸 접속"
    echo "  restart   : 운영 컨테이너 재시작"
    echo "  update    : GHCR 최신 이미지 pull 및 force-recreate"
    echo "  fetch     : 수집 실행 (예: $0 fetch kr, $0 fetch us, $0 fetch all)"
    echo "  status    : 컨테이너 실행 상태 확인"
    echo "============================================================"
    exit 1
    ;;
esac