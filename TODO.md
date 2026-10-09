# TODO.md

## 현재 진행할 작업
* [ ] 실제 목표 비중 입력 (`/target set …`, 사용자 결정)
* [ ] 배당 기록 누락 확인: 원장 배당이 2026-09-02부터만 존재 (월분배 ETF·삼성전자 분기 배당 등 05~08월 기록 없음) → 입금 내역과 대조 후 날짜가 분명한 건은 `/trade` 배당으로 원래 일자에 입력, 출처 불명 소액은 `/recon`으로 정리

## 완료된 직전 작업
- [x] 2026-10-09 봇 상태 점검 `/health` + 매일 16:30 자동 점검 (Prometheus/Grafana 대신 경량 방식, `core/health.py`, 메뉴 '🩺 상태 점검')
  - 배치(16:00 결산·평일 08:55 브리핑) 실행 여부, 결산 스냅샷, 보유 종목 자산군별 시세 최신성(KOSPI·S&P500 최신 거래일 기준이라 휴장일 오탐 없음), 환율, 지수 수집 중단, 최근 24시간 에러 로그(재시작 시 CancelledError 제외)
  - 16:30 점검은 문제 항목만 알림, 같은 항목은 하루 1회 (`anomaly_alert_logs` event_type `HEALTH`)
- [x] 2026-10-09 계좌 예수금 잔액 대사 `/recon`, 개발 컨테이너 운영 `.env` 읽기 차단
- [x] 2026-10-09 성과 분석·차트 고도화(TWR·낙폭·월별·기여도·환율·배당), 주간 리포트, `/alert`, 목표 비중 리밸런싱 알림 (봇 실기동 확인, 운영 배포·한글 폰트·시세 공백 보정 반영 완료)
  - 상세 내역은 `docs/archive/todo_archive.md` (성과 분석 / 이상징후 감시 / 거래 입력 · 원장 / 인프라)

## 근미래 작업 (Next Steps)
* [ ] 장중 FDR 당일 행 제공 여부 검증 (국내 정규장 / 미국 정규장 중 실제 알림 발생 확인)
* [ ] 미사용 `v_latest_asset_breakdown` 뷰 정리 여부 결정 (`/breakdown`은 Python 집계로 전환됨, `get_latest_asset_class_summary`만 참조)
* [ ] 운영 서버 `.venv`를 `requirements.txt`와 동기화 (스크립트 실행 시 `pyupbit` 누락 발생), 스크립트는 `python -m scripts.<name>`으로 실행
* [ ] 일일 펀드 수집 `collect_fund_prices`에 KOFIA 기준가 API를 보조 소스로 추가 검토 (펀드닥터 장애 대비)
* [ ] `/pnl` 하단 리스크 요약 한 줄(현재 낙폭·MDD) 추가 여부 결정
* [ ] 로그 파일 회전: `main.py`가 `FileHandler`라 `logs/app.log`가 무한 증가 → `RotatingFileHandler`(예: 5MB × 5개)로 교체
* [ ] 배치 감사 로그 상태 판정 정비: `record_batch_audit_log`가 옛 배치명(`morning_1030`) 기준이라 `morning_0845`는 항상 INFO, `closing_1600`은 국내 휴장일에 WARNING 오탐

## 보류
* [ ] (보류 2026-10-09) 증권사 캡처 이미지 → Gemini Vision 추출 → 확인 카드 저장
  - 버튼 단계 입력(종목 검색·단가 경고·확인 카드)으로 입력 부담이 해소되어 우선순위 하락. 다건 체결 일괄 입력 수요가 생기면 재검토
* [ ] 이상징후 감시 체커에 Gemini Flash 요약 코멘터리 결합 (시장 전체 하락인지 개별 종목 이슈인지 한 줄)