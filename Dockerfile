# syntax=docker/dockerfile:1.6
#
# 운영용 이미지 (멀티스테이지 빌드)
# - 최종 이미지에 소스 / pip 캐시 / 빌드 도구 미포함 → 이미지 크기 최소화
# - 핫 리로드(ruff/watchdog) 없이 안정적인 단일 진입점 `python main.py`
# - Python 3.11-slim 기반 (C 확장 호환성 위해 alpine 회피)
#
# 빌드/푸시: GitHub Actions 가 멀티스테이지 결과를 ghcr.io 에 푸시
#  예: ghcr.io/<owner>/asset-management-bot:latest

# ---------- stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 빌드 의존성 (mariadb C 확장 컴파일용) — 운영 이미지엔 들어가지 않음
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libmariadb-dev \
        libmariadb3 \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 운영 의존성만 설치
COPY requirements.txt /build/
RUN pip install --upgrade pip \
    && pip install --prefix=/install -r /build/requirements.txt

# 소스 복사
COPY . /build/

# ---------- stage 2: runtime ----------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 런타임에 필요한 mariadb 런타임 라이브러리만 설치 (헤더/컴파일러는 제외)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmariadb3 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash bot

WORKDIR /app

# builder 에서 설치한 Python 패키지 복사
COPY --from=builder /install /usr/local

# 소스 복사 (운영은 이미지 자체에 포함, 볼륨 마운트 X)
COPY --from=builder --chown=bot:bot /build/ /app/

USER bot

# 컨테이너 헬스체크 (단순 import — 봇 프로세스 기동 가능 여부 확인)
HEALTHCHECK --interval=60s --timeout=5s --retries=3 --start-period=30s \
    CMD python -c "import bot.bot" || exit 1

CMD ["python", "main.py"]
