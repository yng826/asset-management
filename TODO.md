# TODO.md

## 현재 진행할 작업
* [ ] 실제 목표 비중 입력 (`/target set …`, 사용자 결정)
* [ ] 배당·이자: 건별 입력 대신 월 1회 `/recon`(💰 이자·기타 수익)으로 반영

## 완료된 직전 작업
- [x] 2026-10-09 DB 접근·중복 코드 공통화 리팩토링 (동작 변경 없음, 상세는 `docs/archive/todo_archive.md` '코드 구조 · 리팩토링')
- [x] 2026-10-09 `/chart alloc` 직접 입력을 버튼 경로와 통일, 차트 메뉴에서 '🥧 자산 배분' 버튼을 맨 뒤로
  - 이전 완료 내역은 `docs/archive/todo_archive.md` (카테고리별, 최신순)

## 근미래 작업 (Next Steps)
* [ ] 장중 FDR 당일 행 제공 여부 검증 (국내 정규장 / 미국 정규장 중 실제 알림 발생 확인)
* [ ] 미사용 `v_latest_asset_breakdown` 뷰 정리 여부 결정 (`/breakdown`은 Python 집계로 전환됨, 호출처 없는 `AssetRepository.get_latest_asset_class_summary`만 참조)
* [ ] 운영 서버 `.venv`를 `requirements.txt`와 동기화 (스크립트 실행 시 `pyupbit` 누락 발생), 스크립트는 `python -m scripts.<name>`으로 실행
* [ ] 일일 펀드 수집 `collect_fund_prices`에 KOFIA 기준가 API를 보조 소스로 추가 검토 (펀드닥터 장애 대비)
* [ ] `/pnl` 하단 리스크 요약 한 줄(현재 낙폭·MDD) 추가 여부 결정

## 보류
* [ ] (보류 2026-10-09) 증권사 캡처 이미지 → Gemini Vision 추출 → 확인 카드 저장
  - 버튼 단계 입력(종목 검색·단가 경고·확인 카드)으로 입력 부담이 해소되어 우선순위 하락. 다건 체결 일괄 입력 수요가 생기면 재검토
* [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합 (시장 전체 하락인지 개별 종목 이슈인지 한 줄)