#!/bin/bash
# ==============================================================================
# k3s 수집기 관리 스크립트 (NUC에서 실행, 저장소 루트 어디서든 동작)
#   ./scripts/k3s.sh deploy            git pull + kubectl apply -k deploy/k3s
#   ./scripts/k3s.sh run <cronjob>     CronJob 에서 수동 Job 생성 후 로그 따라가기 (예: run trades-kr)
#   ./scripts/k3s.sh check             한투 API 점검 Job 생성 후 로그 확인
#   ./scripts/k3s.sh logs <job>        Job 로그
#   ./scripts/k3s.sh status            CronJob / 최근 Job 목록
# 수동 Job 은 이름이 manual-<cronjob> 으로 고정되고, 같은 이름이 남아 있으면 먼저 지운다.
# ==============================================================================
set -euo pipefail

NS="asset-collector"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

case "${1:-}" in
  deploy)
    git pull
    kubectl apply -k deploy/k3s
    ;;

  run)
    [ -n "${2:-}" ] || { echo "사용법: $0 run <cronjob 이름 (collector- 접두사 제외)>"; exit 1; }
    JOB="manual-$2"
    kubectl -n "$NS" delete job "$JOB" --ignore-not-found
    kubectl -n "$NS" create job --from="cronjob/collector-$2" "$JOB"
    kubectl -n "$NS" wait --for=condition=ready pod -l job-name="$JOB" --timeout=60s || true
    kubectl -n "$NS" logs -f "job/$JOB" || true
    ;;

  check)
    NAME=$(kubectl -n "$NS" create -f deploy/k3s/broker-check-kis-job.yaml -o name)
    kubectl -n "$NS" wait --for=condition=ready pod -l job-name="${NAME#*/}" --timeout=60s || true
    kubectl -n "$NS" logs -f "$NAME" || true
    ;;

  logs)
    [ -n "${2:-}" ] || { echo "사용법: $0 logs <job 이름>"; exit 1; }
    kubectl -n "$NS" logs "job/$2"
    ;;

  status)
    kubectl -n "$NS" get cronjobs
    kubectl -n "$NS" get jobs
    ;;

  *)
    echo "사용법: $0 {deploy|run <cronjob>|check|logs <job>|status}"
    exit 1
    ;;
esac
