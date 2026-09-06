import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd


def render_comparison_chart(data: dict) -> io.BytesIO:
    """
    포트폴리오와 벤치마크 지수 수익률 비교 차트를 생성하여 메모리 버퍼로 반환.
    """
    # 티커 이름 매핑 보완
    name_map = {
        "KS11": "KOSPI",
        "KQ11": "KOSDAQ",
        "US500": "S&P 500",
        "IXIC": "NASDAQ",
        "KRW-BTC": "Bitcoin",
    }

    plt.figure(figsize=(10, 6))
    plt.style.use("seaborn-v0_8-whitegrid")

    dates = pd.to_datetime(data["dates"])

    # 1. 벤치마크 지수 그리기 (동적 컬러 팔레트 적용)
    cmap = plt.get_cmap("tab10")
    for i, (ticker, returns) in enumerate(data["benchmarks"].items()):
        label = name_map.get(ticker, ticker)
        color = cmap(i)
        plt.plot(dates, returns, label=label, linestyle="--", alpha=0.7, color=color)

        # 마지막 포인트 수치 표시
        last_val = returns[-1]
        plt.text(dates[-1], last_val, f" {last_val:+.1f}%", fontsize=9, color=color, va="center")

    # 2. 내 포트폴리오 그리기 (딥 네이비 강조)
    portfolio_color = "#1E293B"
    plt.plot(dates, data["portfolio"], label="My Portfolio", color=portfolio_color, linewidth=3)
    last_p = data["portfolio"][-1]
    plt.text(
        dates[-1],
        last_p,
        f" {last_p:+.1f}%",
        fontsize=10,
        fontweight="bold",
        color=portfolio_color,
        va="center",
    )

    # 3. 차트 스타일링
    plt.axhline(0, color="black", linestyle="-", linewidth=0.8, alpha=0.5)
    plt.title("Performance Comparison (Cumulative Return %)", fontsize=14, fontweight="bold")
    plt.xlabel("Date", fontsize=12)
    plt.ylabel("Return (%)", fontsize=12)
    plt.legend(loc="upper left", frameon=True, fontsize=10)
    plt.tight_layout()

    # 4. 메모리 버퍼 저장
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    buf.seek(0)
    plt.close()

    return buf
