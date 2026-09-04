# TODO.md

## 완료된 직전 작업

- [x] MariaDB 연결 설정 및 schema.sql 적용
- [x] 텔레그램, gemini 연동으로 텍스트/음성 채팅을 통한 자료입력

## 현재 진행할 작업

- [ ] transactions 초기 잔고 CSV 일괄 임포트 스크립트 실행
- [ ] FDR 기반 국내 주식/ETF 당일 종가 수집 및 daily_prices UPSERT 함수 작성
- [ ] `/status` 실행 시 daily_prices 종가와 매핑하여 최신 평가액 출력 검증
