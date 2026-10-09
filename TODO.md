# TODO.md

## 현재 진행할 작업
- [ ] 텔레그램으로 명령을 더 편리하게 수정
  - [ ] 현재는 `/pnl` 은 터치로할 수 있지만 기간을 넣으려면 `/pnl 1w` 처럼 타이핑 해야함
  - `/pnl` `1w 2w 1m 3m` 처럼 선택할 수 있는 버튼으로 이어가면 좋겠음.
  - [ ] 수동으로 입력하는 방법도 있어야 할듯

## 완료된 직전 작업
- [x] 2026-10-09 리포트 손익 기준 통일 및 시세 확정값 기록 체계 정비 (개발·운영 DB 적용 완료)
  - `/status`: 계좌별 예수금·총자산 표시, 계좌 유형(일반/연금·절세/가상자산)별 묶음, 기준 시세 일자 범위·시세 지연 경고, 환율(적용일) 표시
  - "오늘 손익" → "최근 결산 손익"으로 통일: 16:00 결산(`closing_1600`) 확정 스냅샷 기준, `/status`·`/breakdown`·`/pnl` 수치 일치 (장중 변동은 `/live`)
  - `/breakdown`: `v_latest_asset_breakdown` 뷰(해외주식 USD 미환산·매도 미차감·예수금 누락) 대신 `/status` 평가 결과로 자산군 집계, 결산일 자산군별 손익 분해(합계 = `/pnl`)
  - `/pnl`: 16:00 결산 전 당일 행 "(잠정)" 표기, `get_daily_pnl_history` `self` 누락 버그 수정
  - `/live` 코인: 등락 기준을 업비트 전일 종가(09:00 KST)로 변경
  - 가상자산 시세: 장중 현재가 대신 확정 일봉 종가만 기록(매일 09:01 수집), 결산 스냅샷 D는 D-1 종가 사용 → 09-10~10-08 코인 시세 재적재 및 04-01~10-08 스냅샷 재계산 (`/pnl` 과거값 변경)
  - 국내 시세: 최근 5영업일 종가 덮어쓰기(16:00 미확정 종가 보정) + 평일 08:55 최근 4일 결산 스냅샷 재계산(`refresh_recent_snapshots`)
  - 지수: FDR KRX 소스 장애(09-18~)로 KOSPI·KOSDAQ Yahoo 소스 전환, 09-18~10-08 백필
  - `daily_holding_snapshots` 일자 단위 교체 저장(계좌명 변경 시 옛 행 잔존 해소), `repair_history` 종료일 기본값 어제·미확정 일봉/정기예금 제외

## 근미래 작업 (Next Steps)
* [ ] 시차 자산(미국주식 T+1, 펀드 NAV T+1/T+2) 시세 수집 시점 및 일일 스냅샷 정합성 검증
  - 미국 주식/펀드의 직전 유효 시세(Latest Available Price) Fallback 매커니즘 점검
  - 휴장일(공휴일/주말) 및 시차 반영 시 daily_snapshots 평가액 왜곡 방지 로직 확인
* [ ] 텔레그램 커맨드로 이상징후 감시 기준값(detector_settings) 조회/변경 기능 추가
  - 예: `/alert` 현재 기준값 목록, `/alert set kr_prev_close_drop_pct 4.0` 변경 (허용 키·값 범위 검증)
* [ ] 장중 FDR 당일 행 제공 여부 검증 (국내 정규장 / 미국 정규장 중 실제 알림 발생 확인)
* [ ] 미사용 `v_latest_asset_breakdown` 뷰 정리 여부 결정 (`/breakdown`은 Python 집계로 전환됨, `get_latest_asset_class_summary`만 참조)
* [ ] 개발 컨테이너가 바인드 마운트된 운영 `.env`를 `load_dotenv()`로 함께 읽는 문제 차단 (`.env.dev`에 없는 키는 운영 값으로 대체됨)
* [ ] 운영 서버 `.venv`를 `requirements.txt`와 동기화 (스크립트 실행 시 `pyupbit` 누락 발생), 스크립트는 `python -m scripts.<name>`으로 실행
* [ ] 계좌 잔액 대사(Reconciliation) 기능: 실제 현금잔액 입력 시 원장과의 차액을 해당 일자 보정 거래(DEPOSIT/WITHDRAW)로 기록
  - 누락된 이자·소액 배당 등을 주기적으로 흡수 (텔레그램 커맨드 또는 CSV 입력)
* [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합
* [ ] MDD 및 샤프 지수(Sharpe Ratio) 리스크 분석 모듈 추가
* [ ] Prometheus / Grafana 기반 모니터링 메트릭 연동