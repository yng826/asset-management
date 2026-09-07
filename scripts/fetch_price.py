"""
자산군별 개별/일괄 시세 수집 수동 실행 스크립트
사용법:
  python scripts/fetch_price.py kr      # 국내 주식만 수집
  python scripts/fetch_price.py us      # 미국 주식만 수집
  python scripts/fetch_price.py fund    # 펀드 NAV만 수집
  python scripts/fetch_price.py crypto  # 코인 시세만 수집
  python scripts/fetch_price.py fx      # 환율만 수집
  python scripts/fetch_price.py all     # 전체 수집 (기본값)
"""

import sys

try:
    from core.fetcher import (
        collect_crypto_prices,
        collect_fund_prices,
        collect_fx_rate,
        collect_holdings_prices,
        collect_kr_prices,
        collect_us_prices,
    )
except ImportError:
    print("❌ 프로젝트 루트에서 실행하거나 PYTHONPATH를 설정하세요.")
    sys.exit(1)


def main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    runners = {
        "kr": ("🇰🇷 국내 주식/ETF 수집", collect_kr_prices),
        "us": ("🇺🇸 미국 주식 수집", collect_us_prices),
        "fund": ("📊 펀드 NAV 수집", collect_fund_prices),
        "crypto": ("🪙 가상자산 시세 수집", collect_crypto_prices),
        "fx": ("💱 환율(USD/KRW) 수집", collect_fx_rate),
        "all": ("🚀 전체 자산 통합 수집", collect_holdings_prices),
    }

    if target not in runners:
        print(f"⚠️ 알 수 없는 옵션: {target}")
        print("사용 가능 옵션: kr, us, fund, crypto, fx, all")
        sys.exit(1)

    label, func = runners[target]
    print(f"\n{label} 시작...")
    result = func(verbose=True)
    print("\n✅ 수집 완료!")


if __name__ == "__main__":
    main()