"""scripts/sync_ticker_master.py - 종목 마스터(ticker_master) 수동 동기화

사용법:
  python -m scripts.sync_ticker_master
"""

import logging

from core.ticker_master import sync_ticker_master

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    print(sync_ticker_master())
