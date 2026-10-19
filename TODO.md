# TODO.md

## 현재 진행할 작업
* [ ] 시세 수집 k3s 이관 (`deploy/k3s/README.md`)
  - [x] 수집 파이프라인 분리(`core/collection.py`), 수집기 진입점·이미지(`collector/`), CronJob 8개, CI 이미지 푸시
  - [x] k3s 배포 (NUC, MariaDB 주소·Secret·GHCR pull Secret), 섀도 운영 시작 (`daily_prices_k3s`)
  - [ ] 섀도 운영 며칠 뒤 `collector compare`로 봇 수집 결과와 비교
  - [ ] ConfigMap `PRICE_TABLE=daily_prices` 전환 후 운영 `.env` 에 `COLLECTOR_EXTERNAL=1` 적용해 봇 수집 중단
  - [ ] 봇 발송 전 당일 수집 감사 로그 확인 (없으면 대기 후 "수집 실패" 경고 발송)
* [ ] 수집기 지수 확장 (국가 지수 → 인기 섹터 → 시간 단위)
  - [x] 1단계: 국가·해외 지수 10개 `IDX:*` 일봉, 수집기 `indices` 작업 + `collector-indices` CronJob(매일 07:30)
  - [ ] (NUC 배포 완료) 첫 실행에서 Yahoo 심볼 10개 수신 확인, 섀도 테이블에 약 3년 백필
  - [ ] 2단계: 인기 섹터 (KODEX 섹터 ETF 가격 대용 검토, 미국은 SOX·S&P500 섹터)
  - [ ] 바닥권에서 반등하는 지수 알림 (데이터가 쌓인 뒤)
* [ ] 증권사 Open API 도입 (로드맵: 프로젝트 파일 `plans/broker-api/00-roadmap.md`)
  - [x] 1단계 공통 틀 + 토스 클라이언트 (PR #5, #6)
  - [x] 2단계 시세 섀도 `daily_prices_broker`, 폴백은 종목별 FDR (PR #7). CronJob `broker-prices-kr/us` 운영 중
  - [ ] 2단계 마무리: 1주 뒤 `compare 7 daily_prices_broker` 비교 후 결과 기록
  - [x] 3단계 체결 대기 테이블 `broker_executions` + 수집기 `trades` + 봇 승인 버튼 (PR #12~#16). 장중 10분 주기 수집(`trades-kr-live`, `trades-us-evening`, `trades-us-night`), 총액=수량×단가(수수료 제외)
  - [x] 4a 한투 클라이언트·계좌별 앱키 (PR #8, #9), 4c-1 연금 계좌 잔고 (PR #10, #11). IRP의 펀드·정기예금은 API 응답에 없어 계속 수동 관리
  - [x] 4b 한투 체결을 대기 테이블에 연결 (PR #17, `EXEC_BROKERS=toss,kis`), 운영 스크립트 `scripts/k3s.sh` (PR #18). NUC에서 한투 10-02 체결 1건이 PENDING으로 들어오는 것까지 확인
  - [ ] 확인 대기: 첫 미국 체결 때 `trade_date` 기준(현지 날짜로 구현, 수동 입력 날짜와 맞는지)
  - [ ] 확인 대기: 한투 응답 필드명은 실제 체결 1건으로만 확인함. 첫 정정·취소 주문 때 정정 묶음(최초 주문 기준 한 건, 잔량 있으면 장 마감까지 보류) 동작 실사
  - [ ] 5단계 시세 출처를 증권사로 전환 (k3s 전환 이후), 보유 잔고↔원장 수량 주 1회 대조(옵션)
  - [ ] 미래에셋: Open API 없음으로 결론(혁 확인 2026-10-19, 디지털엑스는 가상자산 거래소용). API 계획은 보류(`plans/broker-api/05-mirae-asset.md`), 알림 메시지 파싱으로 거래내역·배당/이자·잔고 기록하는 방식을 별도 계획
    - 알림 붙여넣기 파싱은 이미 있음(PR #20 `account_hints`). 미래에셋 알림 샘플(체결·입금·배당)이 잘 파싱되는지 확인하고 모자란 형식만 보완
    - 종가 출처를 미래에셋으로 바꾸는 구현은 다른 증권사를 안 쓰게 될 때 별도 진행(메모만, API 없으면 대안 재검토)
* [ ] 실제 목표 비중 입력 (`/target set …`, 사용자 결정)
* [ ] 배당·이자: 건별 입력 대신 월 1회 `/recon`(💰 이자·기타 수익)으로 반영

## 완료된 직전 작업
- [x] 2026-10-10 결산·브리핑 스케줄 개편 (개발·운영 적용)
  - 평일 오전 브리핑 08:55 → 09:05 (코인 전일 일봉 09:00 확정 반영), 평일 결산 월~금 16:00
  - 토 10:00 글로벌 리포트 + 토·일 16:00 결산 → 토·일 09:05 주말 결산(`weekend_closing_report`) 하나로 통합. 주말 스냅샷 시세는 09:00이면 모두 확정, 토요일엔 금요일 스냅샷을 금요일 밤 미국 종가로 재계산
  - 감사 로그는 `closing_1600`으로 기록(당일 스냅샷 확정 판단 기준), 판정은 `closing_weekend`(미국·펀드·환율·국내·코인), `/health` 기대 시각 갱신
- [x] 2026-10-10 미국 주식 종가 NaN 처리: FDR 최신 행 종가가 일시적으로 NaN이면 UPSERT 실패 → 종가 있는 행만 사용 (10-10 운영에서 발생, 재실행으로 보충)
- [x] 2026-10-10 배치 수동 실행 및 운영 스크립트 실행 규칙
  - `scripts/run_batch.py` (`morning`·`closing`·`weekend`·`weekly`·`rebalance`·`health`, 발송·감사 로그 포함)
  - `prod.sh batch <이름>` / `prod.sh exec <모듈> [인자]`: 운영 컨테이너에서 배포된 이미지 코드로 실행 → 운영 서버 소스·venv 불필요
  - 운영 서버 `git pull`은 호스트 파일(`prod.sh`·`docker-compose.yml` 등) 변경 시에만 (CONTEXT.md '스크립트 실행 규칙')
- [x] 2026-10-10 CI 빌드 캐시 사용(`no-cache` 제거) 및 `requirements.txt` 운영 pip freeze 기준 버전 고정, 호스트 venv·dev 컨테이너 동기화, `deploy.yml` 주석을 Watchtower 기준으로 정정
- [x] 2026-10-10 헬스체크 수정 (dev·운영 모두 healthy): 이미지에 `procps` 설치, `pgrep -fx 'python main.py'`로 통일 (운영 기존 패턴은 실행 명령과 불일치, dev는 watchmedo까지 잡아 항상 통과)
  - 이전 완료 내역은 `docs/archive/todo_archive.md` (카테고리별, 최신순)

## 근미래 작업 (Next Steps)
* [ ] 장중 FDR 당일 행 제공 여부 검증 (국내 정규장 / 미국 정규장 중 실제 알림 발생 확인)
* [ ] 미사용 `v_latest_asset_breakdown` 뷰 정리 여부 결정 (`/breakdown`은 Python 집계로 전환됨, 호출처 없는 `AssetRepository.get_latest_asset_class_summary`만 참조)
* [ ] 2026-10-11(일) 첫 자동 주말 결산 확인 (08:00 주간 리포트에 금요일 미국 종가 반영, 09:05 주말 결산 발송·`closing_1600` SUCCESS)
* [ ] 일일 펀드 수집 `collect_fund_prices`에 KOFIA 기준가 API를 보조 소스로 추가 검토 (펀드닥터 장애 대비)
* [ ] `/pnl` 하단 리스크 요약 한 줄(현재 낙폭·MDD) 추가 여부 결정

## 보류
* [ ] (보류 2026-10-09) 증권사 캡처 이미지 → Gemini Vision 추출 → 확인 카드 저장
  - 버튼 단계 입력(종목 검색·단가 경고·확인 카드)으로 입력 부담이 해소되어 우선순위 하락. 다건 체결 일괄 입력 수요가 생기면 재검토
* [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합 (시장 전체 하락인지 개별 종목 이슈인지 한 줄)