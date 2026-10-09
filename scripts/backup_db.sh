#!/usr/bin/env bash
# scripts/backup_db.sh
# 운영 DB(asset_management) 일일 백업 스크립트 — 운영 서버 호스트의 crontab 에서 실행
#
# 동작:
#   1) mariadb 컨테이너 안의 mariadb-dump 로 덤프 (서버와 버전 일치, --single-transaction 이라 락 없음)
#   2) backups/ 에 gzip 저장 → 무결성 검사(gzip -t, "Dump completed" 확인) 후 확정
#   3) 보관 기간(기본 14일)이 지난 백업 파일 정리
#   4) 실패 시 텔레그램으로 알림 (성공 시에는 조용히 종료)
#
# 사용법:
#   ./scripts/backup_db.sh
#
# crontab 등록 예 (매일 04:00, 서버 로컬 시간 기준):
#   0 4 * * * /opt/asset-management/scripts/backup_db.sh >> /opt/asset-management/logs/backup.log 2>&1
#
# 복원 (kodi 계정 사용 — 뷰의 DEFINER 가 kodi 이므로):
#   gunzip -c backups/asset_management_YYYYMMDD_HHMMSS.sql.gz \
#     | docker exec -i -e MYSQL_PWD="<비밀번호>" mariadb mariadb -u kodi <대상 DB명>
#
# 환경변수 (선택):
#   MARIADB_CONTAINER   mariadb 컨테이너 이름 (기본: mariadb)
#   BACKUP_DIR          백업 저장 경로 (기본: <프로젝트>/backups)
#   RETENTION_DAYS      보관 일수 (기본: 14)

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/.env"
MARIADB_CONTAINER="${MARIADB_CONTAINER:-mariadb}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

# .env 에서 값 읽기 (따옴표로 감싼 값, 따옴표 없는 값의 줄 끝 주석 처리)
env_get() {
  local line value
  line="$(grep -E "^$1=" "$ENV_FILE" | tail -n 1 || true)"
  value="${line#*=}"
  if [[ "$value" =~ ^\"(.*)\"[[:space:]]*(#.*)?$ ]] || [[ "$value" =~ ^\'(.*)\'[[:space:]]*(#.*)?$ ]]; then
    value="${BASH_REMATCH[1]}"
  else
    value="$(printf '%s' "$value" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')"
  fi
  printf '%s' "$value"
}

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# 텔레그램 HTML 모드 알림 (동적 문자열은 &, <, > 이스케이프)
notify_failure() {
  local token chat_id escaped
  token="$(env_get TELEGRAM_BOT_TOKEN)"
  chat_id="$(env_get TELEGRAM_CHAT_ID)"
  [[ -z "$token" || -z "$chat_id" ]] && return 0
  escaped="$(printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g')"
  curl -s -o /dev/null --max-time 10 "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat_id}" \
    --data-urlencode "parse_mode=HTML" \
    --data-urlencode "text=🚨 <b>DB 백업 실패</b>
${escaped}" || true
}

TMP_FILE=""
fail() {
  log "❌ $1"
  [[ -n "$TMP_FILE" ]] && rm -f "$TMP_FILE"
  notify_failure "$1"
  exit 1
}

[[ -f "$ENV_FILE" ]] || fail ".env 파일 없음: $ENV_FILE"

DB_USER="$(env_get DB_USER)"
DB_PASSWORD="$(env_get DB_PASSWORD)"
DB_NAME="$(env_get DB_NAME)"
[[ -n "$DB_USER" && -n "$DB_NAME" ]] || fail ".env 에 DB_USER / DB_NAME 설정 없음"

mkdir -p "$BACKUP_DIR"
STAMP="$(date '+%Y%m%d_%H%M%S')"
OUT_FILE="$BACKUP_DIR/${DB_NAME}_${STAMP}.sql.gz"
TMP_FILE="$OUT_FILE.partial"

log "▶️  백업 시작: $DB_NAME → $OUT_FILE"

if ! docker exec -e MYSQL_PWD="$DB_PASSWORD" "$MARIADB_CONTAINER" \
  mariadb-dump -u "$DB_USER" --single-transaction --skip-lock-tables --routines --triggers "$DB_NAME" \
  | gzip > "$TMP_FILE"; then
  fail "mariadb-dump 실행 실패 (컨테이너: $MARIADB_CONTAINER, DB: $DB_NAME)"
fi

gzip -t "$TMP_FILE" || fail "gzip 무결성 검사 실패: $TMP_FILE"
gunzip -c "$TMP_FILE" | tail -n 1 | grep -q "Dump completed" || fail "덤프가 끝까지 완료되지 않음: $TMP_FILE"

mv "$TMP_FILE" "$OUT_FILE"
TMP_FILE=""
log "✅ 백업 완료: $(du -h "$OUT_FILE" | cut -f1)"

# 보관 기간이 지난 백업 정리 (이 스크립트가 만든 파일 패턴만 대상)
find "$BACKUP_DIR" -maxdepth 1 -type f -name "${DB_NAME}_*.sql.gz" -mtime +"$RETENTION_DAYS" -print -delete \
  | while read -r removed; do log "🧹 오래된 백업 삭제: $(basename "$removed")"; done
