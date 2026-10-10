# TODO.md

## 현재 진행할 작업
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