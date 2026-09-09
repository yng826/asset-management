-- Active: 1788655200228@@192.168.0.99@3306@asset_management
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

-- 3. 일별 총자산 스냅샷
CREATE TABLE IF NOT EXISTS daily_snapshots (
    snapshot_date DATE NOT NULL,
    total_eval_amount DECIMAL(15, 2) NOT NULL,
    total_invested_amount DECIMAL(15, 2),
    cash_amount DECIMAL(15, 2),
    PRIMARY KEY (snapshot_date)
);

-- 5. 일별 종목별 보유 스냅샷
CREATE TABLE IF NOT EXISTS daily_holding_snapshots (
    snapshot_date DATE NOT NULL,
    account_name VARCHAR(50) NOT NULL,
    ticker_code VARCHAR(100) NOT NULL,
    quantity DECIMAL(15, 4) NOT NULL,
    close_price DECIMAL(15, 4) NOT NULL,
    eval_amount DECIMAL(15, 2) NOT NULL,
    invested_amount DECIMAL(15, 2) NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (snapshot_date, account_name, ticker_code)
);


-- 4. 배치 실행 로그
CREATE TABLE IF NOT EXISTS batch_execution_logs (
    log_id INT AUTO_INCREMENT PRIMARY KEY,
    batch_name VARCHAR(50) NOT NULL,              -- 'morning_1030', 'closing_1600' 등
    execution_date DATE NOT NULL,                 -- 기준 일자
    execution_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, -- 실제 적재 시각
    status VARCHAR(20) NOT NULL,                  -- 'SUCCESS', 'WARNING', 'FAILED'
    us_count INT DEFAULT 0,                       -- 미국 주식 수집 건수
    fx_count INT DEFAULT 0,                       -- 환율 수집 건수
    fund_count INT DEFAULT 0,                     -- 펀드 수집 건수
    kr_count INT DEFAULT 0,                       -- 국내 주식 수집 건수
    crypto_count INT DEFAULT 0,                   -- 코인 수집 건수
    snapshot_created TINYINT(1) DEFAULT 0,        -- 일일 총자산 스냅샷 생성 여부 (1/0)
    message TEXT                                  -- 비고 및 요약 메시지
);




##################### VIEWS #####################
-- 1. 일자별·계좌별 평가액 및 비중 뷰
CREATE OR REPLACE VIEW v_daily_account_summary AS
SELECT 
    h.snapshot_date,
    h.account_name,
    COUNT(*) AS holding_count,
    SUM(h.eval_amount) AS account_eval,
    SUM(h.invested_amount) AS account_invested,
    ROUND((SUM(h.eval_amount) - SUM(h.invested_amount)) / NULLIF(SUM(h.invested_amount), 0) * 100, 2) AS return_pct,
    ROUND(SUM(h.eval_amount) / s.total_eval_amount * 100, 2) AS weight_pct
FROM daily_holding_snapshots h
JOIN daily_snapshots s ON h.snapshot_date = s.snapshot_date
GROUP BY h.snapshot_date, h.account_name, s.total_eval_amount;

-- 2. 일자별·자산군별(코인/국내주식/해외주식/펀드/현금) 비중 뷰
CREATE OR REPLACE VIEW v_daily_asset_class_summary AS
SELECT 
    h.snapshot_date,
    CASE 
        WHEN h.ticker_code LIKE 'KRW-%' THEN '가상자산'
        WHEN h.ticker_code REGEXP '^[0-9]{6}$' THEN '국내주식/ETF'
        WHEN h.ticker_code REGEXP '^[A-Z]{1,5}$' THEN '해외주식'
        WHEN h.ticker_code REGEXP '^(KR5|K55)' OR h.ticker_code LIKE '4.42|%' THEN '펀드/퇴직예치'
        WHEN h.ticker_code LIKE '%CASH%' OR h.ticker_code = 'KRW' THEN '현금/예수금'
        ELSE '기타'
    END AS asset_class,
    COUNT(*) AS item_count,
    SUM(h.eval_amount) AS class_eval,
    SUM(h.invested_amount) AS class_invested,
    ROUND(SUM(h.eval_amount) / s.total_eval_amount * 100, 2) AS weight_pct
FROM daily_holding_snapshots h
JOIN daily_snapshots s ON h.snapshot_date = s.snapshot_date
GROUP BY h.snapshot_date, asset_class, s.total_eval_amount;

-- 3. 최신일 기준 전체 보유 종목 상세 순위 뷰 (비중 및 단가 포함)
CREATE OR REPLACE VIEW v_latest_holding_ranking AS
SELECT 
    h.snapshot_date,
    h.account_name,
    h.ticker_code,
    h.quantity,
    h.close_price,
    h.eval_amount,
    h.invested_amount,
    ROUND(h.eval_amount - h.invested_amount, 2) AS profit_amount,
    ROUND((h.eval_amount - h.invested_amount) / NULLIF(h.invested_amount, 0) * 100, 2) AS return_pct,
    ROUND(h.eval_amount / s.total_eval_amount * 100, 2) AS weight_pct
FROM daily_holding_snapshots h
JOIN daily_snapshots s ON h.snapshot_date = s.snapshot_date
WHERE h.snapshot_date = (SELECT MAX(snapshot_date) FROM daily_holding_snapshots)
ORDER BY h.eval_amount DESC;