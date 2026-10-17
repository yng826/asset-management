# 시세 수집기 k3s 이관

봇 프로세스 안의 APScheduler 가 하던 **시세 수집·스냅샷** 작업을 k3s CronJob 으로 옮긴다.
봇은 텔레그램 대화(거래·입출금 기록, 조회)·리포트 발송·이상징후 알림만 맡고, DB(MariaDB)는 지금 위치 그대로 공유한다.

## 역할 분담

| 기존 APScheduler 작업 | 시각 (KST) | 이관 후 담당 | CronJob |
|---|---|---|---|
| `probe_0830/0840/0850_job` 사전 수집 점검 | 평일 08:30·40·50 | 수집기 | `collector-probe-0830` 외 2개 |
| `crypto_daily_close_job` 코인 전일 일봉 | 매일 09:01 | 수집기 | `collector-crypto-close` |
| `morning_briefing_job` 의 수집·스냅샷 재계산·감사 로그 | 평일 09:05 | 수집기 09:02 | `collector-morning` |
| `weekend_closing_report_job` 의 수집·스냅샷·감사 로그 | 토·일 09:05 | 수집기 09:02 | `collector-weekend` |
| `daily_closing_report_job` 의 수집·당일 스냅샷·감사 로그 | 평일 16:00 | 수집기 15:55 | `collector-closing` |
| `ticker_master_sync_job` 종목 마스터 | 일 07:00 | 수집기 | `collector-ticker-master` |
| (신규) 국가·해외 지수 일봉 `IDX:*` | 매일 07:30 | 수집기만 | `collector-indices` |
| 브리핑·결산 **발송** (09:05, 16:00) | 그대로 | 봇 | – |
| 이상징후 감시 5종, 주간 리포트, 리밸런싱 알림, 상태 점검 | 그대로 | 봇 | – |
| `ticker_master_ensure_job` (기동 시 마스터 비어 있으면 1회 적재) | 기동 시 | 봇 | – |

브리핑·결산은 "수집 → 발송" 한 함수였으므로 수집을 발송 3~5분 전으로 당겼다. 수집 내용과 감사 로그 이름(`morning_0845`, `closing_1600`, `probe_*`)은 같아서 `/health` 판정은 그대로 동작한다.

이상징후 감시는 실시간 시세를 조회만 하고 알림을 보내는 작업이라 봇에 남겼다 (DB 시세를 채우지 않음).

## 구성

```text
collector/                 수집기 진입점 (python -m collector <job>) 과 이미지
  __main__.py              작업 목록: morning closing weekend probe crypto-close indices ticker-master
  Dockerfile               봇 의존성(telegram·genai·matplotlib) 없는 이미지
  requirements.txt
core/collection.py         봇 스케줄러와 수집기가 함께 쓰는 수집 파이프라인
core/fetcher/index.py      국가·해외 지수 목록(INDICES)과 수집 함수 (수집기 전용)
deploy/k3s/
  namespace.yaml           asset-collector 네임스페이스
  mariadb-external.yaml    클러스터 밖 MariaDB 를 Service 'mariadb' 로 노출 (Service + EndpointSlice)
  configmap.yaml           DB_HOST·DB_NAME·TZ 등 비밀 아닌 설정
  secret.example.env       DB_USER·DB_PASSWORD 템플릿 (실제 값은 collector.env, 커밋 금지)
  cronjobs.yaml            CronJob 9개 (timeZone: Asia/Seoul)
  compare-job.yaml         섀도 비교 1회 Job (수동 kubectl create)
  broker-check-job.yaml    증권사(토스) API 점검 1회 Job (수동 kubectl create)
  broker-check-kis-job.yaml  증권사(한투) API 점검 1회 Job (수동 kubectl create)
  kustomization.yaml
```

이미지는 `main` push 시 GitHub Actions 가 `ghcr.io/yng826/asset-management-collector:latest` 로 푸시한다.

## 처음 배포

```bash
# 0) mariadb-external.yaml 의 addresses 를 MariaDB 가 3306 을 연 호스트 IP 로 수정
#    (k3s 파드에서 접근하려면 MariaDB 컨테이너가 호스트 포트 3306 을 publish 해야 함)

# 1) 네임스페이스와 Secret
kubectl apply -f deploy/k3s/namespace.yaml
cp deploy/k3s/secret.example.env deploy/k3s/collector.env   # 값 입력
kubectl -n asset-collector create secret generic collector-env --from-env-file=deploy/k3s/collector.env
# collector.env 값은 따옴표 없이 (DB_PASSWORD=abc#123). --from-env-file 은 따옴표까지 값에 포함함

# 2) GHCR 이미지 pull 용 Secret (패키지가 private 인 경우, read:packages 권한 PAT)
kubectl -n asset-collector create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io --docker-username=yng826 --docker-password=<PAT>

# 3) 나머지 리소스
kubectl apply -k deploy/k3s
kubectl -n asset-collector get cronjobs
```

## 확인 · 수동 실행

```bash
# 스케줄을 기다리지 않고 1회 실행 (DB 에 실제로 씀, UPSERT 라 재실행 안전)
kubectl -n asset-collector create job --from=cronjob/collector-crypto-close test-crypto-1
kubectl -n asset-collector logs -f job/test-crypto-1
kubectl -n asset-collector get jobs          # COMPLETIONS 1/1 이면 성공, 실패는 종료 코드 1

# 일시 중지 / 재개
kubectl -n asset-collector patch cronjob collector-morning -p '{"spec":{"suspend":true}}'
```

## 국가·해외 지수 (`indices`)

- 목록은 `core/fetcher/index.py` 의 `INDICES` (KOSPI200, 나스닥100, 닛케이, 상해, 항셍, 대만, 인도 Nifty50, 유로스톡스50, DAX, FTSE100).
  기존 벤치마크(KS11·KQ11·US500·IXIC·DJI)는 봇과 같은 수집 함수가 계속 맡는다.
- 저장 코드는 `IDX:` 접두어 (`IDX:N225`), 값은 각 지수의 원래 포인트(통화 환산 없음). 출처는 FDR 의 Yahoo 경로.
- 매번 최근 7일을 전부 UPSERT 하므로 하루 빠져도 다음 실행에서 메워진다. 일부 지수만 실패해도 Job 은 실패로 표시.
- 봇은 이 지수를 모으지 않으므로 섀도 비교(`compare`)에서는 제외된다.
- 과거치 백필 (약 3년, `PRICE_TABLE` 대상 테이블에 씀. 본 테이블 전환 뒤 한 번 더 실행):
  ```bash
  kubectl -n asset-collector create job idx-backfill --from=cronjob/collector-indices --dry-run=client -o yaml \
    | sed 's/^\(\s*\)- indices$/\1- indices\n\1- "1100"/' | kubectl create -f -
  kubectl -n asset-collector logs -f job/idx-backfill
  ```

로컬에서 이미지 없이 확인: `ENV_FILE=.env.dev .venv/bin/python -m collector morning`

## 전환 순서

1. **병행(섀도) 운영**: ConfigMap 의 `PRICE_TABLE: daily_prices_k3s` 그대로 배포. 수집기는 시세만 `daily_prices_k3s` 에 쌓고, 스냅샷·자가 치유·감사 로그·종목 마스터는 건드리지 않는다. 봇은 지금처럼 모든 일을 계속한다.
   - 수집기 DB 계정에 `daily_prices_k3s` 생성 권한이 필요 (첫 실행 때 `CREATE TABLE ... LIKE daily_prices` 로 자동 생성).
   - 며칠 뒤 비교 (읽기 전용 1회 Job, kustomization 에는 포함하지 않음):
     ```bash
     kubectl create -f deploy/k3s/compare-job.yaml
     kubectl -n asset-collector logs -l app.kubernetes.io/component=compare --tail=100
     ```
     로컬에서는 `PRICE_TABLE=daily_prices_k3s python -m collector compare 7`
   - 수집 시각 차이(수집기 15:55, 봇 16:00 등)로 미확정 종가가 다를 수 있으니, 다음 날 아침 재수집 뒤의 값으로 판단.
2. **본 테이블로 전환**: ConfigMap 을 `PRICE_TABLE: daily_prices` 로 바꿔 `kubectl apply -k deploy/k3s`. 이때부터 수집기도 스냅샷·감사 로그까지 맡는다 (봇과 잠시 겹쳐도 UPSERT 라 데이터는 같고 감사 로그만 두 줄).
3. **봇에서 수집 끄기**: 운영 `.env` 에 `COLLECTOR_EXTERNAL=1` 추가 후 `docker compose up -d` (env 변경은 Watchtower 가 반영하지 않음). 봇은 수집 전용 작업을 등록하지 않고, 브리핑·결산은 DB 에 있는 시세로 발송만 한다.
4. **되돌리기**: `COLLECTOR_EXTERNAL` 을 지우고 재기동하면 봇이 다시 수집한다. CronJob 은 `suspend` 로 멈춘다.
5. **정리**: 비교가 끝나면 `daily_prices_k3s` 삭제 가능.

## 증권사 시세 섀도 (`daily_prices_broker`, 로드맵 2단계)
토스 Open API 종가를 국내·미국 보유 종목에 한해 `daily_prices_broker` 에 쌓고 FDR 시세(`daily_prices`)와 비교한다. 운영 리포트와 위 k3s 섀도에는 영향 없음.
- **사전 작업 (DB 관리자, 배포 전 1회)**: `collector` 계정은 새 테이블을 못 만든다.
  ```sql
  CREATE TABLE IF NOT EXISTS daily_prices_broker LIKE daily_prices;
  GRANT CREATE, SELECT, INSERT, UPDATE ON `<DB명>`.daily_prices_broker TO 'collector'@'192.168.0.84';
  FLUSH PRIVILEGES;
  ```
- CronJob `collector-broker-prices-kr`(평일 16:05), `collector-broker-prices-us`(화~토 07:30). env `PRICE_TABLE=daily_prices_broker` 로 ConfigMap 값을 덮어쓴다. 토스 키(`TOSS_*`)는 1단계에서 넣은 `collector-env` 사용.
- 증권사 실패·빈 응답이면 그 종목만 FDR 값으로 채우고 로그에 `출처=fdr(폴백)` 으로 남는다. 폴백 건수: `kubectl -n asset-collector logs -l app.kubernetes.io/component=broker-prices-kr | grep 폴백`
- 수동 1회: `kubectl -n asset-collector create job --from=cronjob/collector-broker-prices-kr bp-manual`
- 1주 이상 쌓은 뒤 비교: compare-job.yaml 의 args 를 `["compare", "7", "daily_prices_broker"]` 로 바꿔 `create`, 또는 로컬 `python -m collector compare 7 daily_prices_broker`.
  - 국내는 완전 일치가 기대값. 차이가 나면 수정주가 설정과 16:00 이전 미확정 값부터 확인.
  - 미국은 날짜 기준 확인: 토스 캔들 timestamp 의 시간대에 따라 하루 밀릴 수 있다(차이율이 날짜 어긋남으로 나타나면 파서 보정).

## 알려진 차이 · 다음 단계

- 수집기 로그는 `kubectl logs` 로만 보인다. 봇의 `/health` 에러 로그 점검(`logs/app.log`)에는 잡히지 않고, 대신 배치 감사 로그(`batch_execution_logs`) 판정은 그대로 반영된다.
- 수집이 3분 안에 끝나지 않으면 09:05 브리핑이 직전 시세로 나갈 수 있다. 실행 시간은 `kubectl get jobs` 의 DURATION 으로 확인.
  다음 단계: 봇이 발송 전에 그날 감사 로그(`morning_0845`·`closing_1600`)를 확인해 없으면 잠시 기다리고, 그래도 없으면 "수집 실패" 경고를 붙여 발송.
- Secret 을 Git 으로 관리하려면 Sealed Secrets 또는 SOPS 도입.
- MariaDB 를 k3s 로 옮기면 `mariadb-external.yaml` 만 StatefulSet + PVC 로 교체.
