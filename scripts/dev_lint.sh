#!/usr/bin/env bash
# scripts/dev_lint.sh
#
# ruff 기반 코드 품질 검사 래퍼.
# 작업 완료 단계에서 "ruff check 통과"를 보장하기 위한 표준 진입점.
#
# 사용법:
#   ./scripts/dev_lint.sh           # check (lint 위반 발견만, 미수정)
#   ./scripts/dev_lint.sh fix        # check --fix (안전한 자동 수정)
#   ./scripts/dev_lint.sh format     # format 검사/적용
#   ./scripts/dev_lint.sh all        # check + format (가장 엄격)
#
# 종료 코드:
#   0 - 모든 검사 통과
#   1 - lint 위반 발견
#   2 - 도구 미설치

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

CMD="${1:-check}"

# ruff 설치 확인 (로컬 .venv 또는 시스템)
if ! command -v ruff >/dev/null 2>&1; then
    if [ -x ".venv/bin/ruff" ]; then
        alias ruff=".venv/bin/ruff"
        RUFF=".venv/bin/ruff"
    else
        echo "❌ ruff 미설치. 다음으로 설치:"
        echo "    pip install -r requirements-dev.txt"
        exit 2
    fi
else
    RUFF="ruff"
fi

case "$CMD" in
  check)
    echo "▶️  ruff check (lint 검사)"
    "$RUFF" check .
    ;;
  fix)
    echo "▶️  ruff check --fix (안전한 자동 수정)"
    "$RUFF" check --fix .
    ;;
  format)
    echo "▶️  ruff format (포매팅 적용)"
    "$RUFF" format .
    ;;
  format-check)
    echo "▶️  ruff format --check (포매팅 차이 검사만)"
    "$RUFF" format --check .
    ;;
  all)
    echo "▶️  ruff check"
    "$RUFF" check .
    echo ""
    echo "▶️  ruff format --check"
    "$RUFF" format --check .
    ;;
  *)
    echo "사용법: $0 {check|fix|format|format-check|all}" >&2
    exit 1
    ;;
esac
