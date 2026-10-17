# syntax=docker/dockerfile:1.6
#
# 시세 수집기 이미지 (k3s CronJob 용, 봇 이미지와 별도)
# - 빌드 컨텍스트는 저장소 루트: docker build -f collector/Dockerfile -t asset-collector .
# - 봇 코드(core/fetcher·database 등)를 그대로 쓰되 봇 전용 의존성(telegram·genai·matplotlib)은 제외
# - 한 번 실행하고 종료: CMD 없이 CronJob 이 args 로 작업 이름 지정 (python -m collector <job>)
#
# 빌드/푸시: GitHub Actions 가 ghcr.io/<owner>/asset-management-collector 로 푸시

# ---------- stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# mariadb C 확장 컴파일용 (런타임 이미지엔 미포함)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libmariadb-dev \
        libmariadb3 \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY collector/requirements.txt /build/
RUN pip install --upgrade pip \
    && pip install --prefix=/install -r /build/requirements.txt

# ---------- stage 2: runtime ----------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmariadb3 \
        tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin collector

WORKDIR /app

COPY --from=builder /install /usr/local

# 수집에 필요한 소스만 복사 (bot/ 제외)
COPY --chown=collector:collector config/ /app/config/
COPY --chown=collector:collector core/ /app/core/
COPY --chown=collector:collector database/ /app/database/
COPY --chown=collector:collector scripts/ /app/scripts/
COPY --chown=collector:collector collector/ /app/collector/

USER 10001

ENTRYPOINT ["python", "-m", "collector"]
CMD ["--list"]
