.PHONY: check test fix

# 검사 및 사운드 알림
check:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .
	.venv/bin/pytest
	@paplay /usr/share/sounds/freedesktop/stereo/complete.oga 2>/dev/null || true

# 귀찮을 때 자동 수정까지
fix:
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .
