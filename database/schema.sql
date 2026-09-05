-- Active: 1770434719604@@127.0.0.1@3306@asset_management
-- 1. 거래 내역 원장 (추가만 됨, 수정/삭제 불필요)
CREATE TABLE IF NOT EXISTS transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trans_date DATE NOT NULL,                        -- 거래 날짜
    account_name VARCHAR(50) NOT NULL,              -- 계좌명 (예: 한투일반계좌)
    ticker_name VARCHAR(100) NOT NULL,             -- 종목명 (예: 삼성전자, 배당금 등)
    ticker_code VARCHAR(100) DEFAULT NULL,           -- 종목코드 (예: 005930)
    action_type ENUM('BUY', 'SELL', 'DIVIDEND', 'DEPOSIT', 'WITHDRAW') NOT NULL, -- 매수/매도/배당/입금/출금
    quantity DECIMAL(15, 4) DEFAULT 0,              -- 수량 (배당/입출금은 0)
    unit_price DECIMAL(15, 2) DEFAULT 0,            -- 단가
    total_amount DECIMAL(15, 2) NOT NULL,           -- 총 거래액/배당액
    memo TEXT DEFAULT NULL,                         -- 원본 자연어 메시지 또는 메모
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. 일별 종가 캐시 (내 계좌와 무관한 순수 시세 저장소)
CREATE TABLE IF NOT EXISTS daily_prices (
    price_date DATE NOT NULL,
    ticker_code VARCHAR(100) NOT NULL,
    close_price DECIMAL(15, 4) NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (price_date, ticker_code)
);