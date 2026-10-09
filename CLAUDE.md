# Project Guidelines for Claude Code

## Development & Quality Rules
- 코드 수정 후 항상 `./scripts/dev_lint.sh all`을 실행하여 ruff 검사 위반 0건을 확인할 것.
- Git 커밋은 사용자가 직접 작성하므로 에이전트는 절대 임의 커밋(`git commit`)을 수행하지 말 것.
- 기존 파일 삭제 금지 (No Blind Deletion) 및 최소 단위 점진적 수정 (Minimal Blast Radius) 원칙 준수.
- 텔레그램 메시지는 HTML 모드(`parse_mode="HTML"`) 기준이며 동적 문자열은 `html.escape()`로 방어할 것.

## Reference Documents
- 전체 아키텍처 및 상세 규칙: CONTEXT.md
- 진행 예정 및 완료 태스크: TODO.md
