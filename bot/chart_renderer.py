import io

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.use("Agg")


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


def render_allocation_chart(data: dict) -> io.BytesIO:
    """
    자산 배분 누적 면적 차트(Stacked Area Chart)를 생성하여 메모리 버퍼로 반환.
    """
    plt.figure(figsize=(10, 6))
    plt.style.use("seaborn-v0_8-whitegrid")

    dates = pd.to_datetime(data["dates"])
    categories = data["categories"]
    weights_dict = data["weights"]

    y_data = [weights_dict[cat] for cat in categories]

    cmap = plt.get_cmap("tab10")
    if y_data:
        plt.stackplot(
            dates,
            y_data,
            labels=categories,
            colors=[cmap(i) for i in range(len(y_data))],
            alpha=0.85,
        )

    plt.ylim(0, 100)
    plt.title("Asset Allocation History (Stacked Area %)", fontsize=14, fontweight="bold")
    plt.xlabel("Date", fontsize=12)
    plt.ylabel("Allocation (%)", fontsize=12)
    plt.legend(loc="upper right", frameon=True, fontsize=10)
    plt.xticks(rotation=45)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    buf.seek(0)
    plt.close()


def render_stack_bar_chart(data: dict) -> io.BytesIO:
    """
    자산군별 절대금액 스택 바 차트를 생성하여 메모리 버퍼로 반환 (다크 테마).
    """
    bg_color = "#131722"
    text_color = "#D1D4DC"
    grid_color = "#2A2E39"

    fig, ax = plt.subplots(figsize=(11, 6), facecolor=bg_color)
    ax.set_facecolor(bg_color)
    ax.set_title(
        "Asset Value Stacked Area Chart (100M KRW)",
        color="#FFFFFF",
        fontsize=13,
        fontweight="bold",
        pad=25,
    )

    dates = pd.to_datetime(data["dates"])
    categories = data["categories"]
    values = data["values"]

    PALETTE = {
        # 1. 메인 1, 2위 자산군: 채도를 낮춘 웜 앰버 & 덜 쨍한 딥 오션블루
        "Crypto": "#B5B67F",  # 쨍한 #F59E0B 대신 묵직하고 차분한 앰버 브라운
        "Global ETF": "#509CBD",  # 눈부신 하늘색 대신 소프트 스카이블루 (또는 #0284C7)
        # 2. 중간 비중 자산군: 부드러운 뮤트 바이올렛 & 소프트 코랄
        "KR ETF": "#8968C4",  # 차분하고 깊이감 있는 모던 퍼플
        "KR Stock": "#E11D48",  # 쨍함을 뺀 로즈 레드
        # 3. 소액/안전 자산군
        "US Stock": "#DB2777",  # 톤다운된 마젠타 핑크
        "Fund/Pension": "#059669",  # 차분한 포레스트/에메랄드 그린
        "Cash": "#475569",  # 다크 테마에 묻어가는 슬레이트 그레이
        "Deposit": "#2563EB",  # 딥 블루
        "Etc": "#64748B",  # 뮤트 그레이
    }

    # 1. 2D 스택 데이터 및 배색 리스트 준비 (루프 없이 1회만 생성)
    y_stacks = [values[cat] for cat in categories]
    colors = [PALETTE.get(cat, "#64748B") for cat in categories]

    # 2. 누적 면적(Area) 차트 1회 호출
    plt.stackplot(
        dates,
        *y_stacks,
        labels=categories,
        colors=colors,
        alpha=0.9,
    )

    ax.xaxis.set_major_locator(
        mdates.DayLocator(interval=3) if len(dates) <= 25 else mdates.AutoDateLocator(minticks=5, maxticks=8)
    )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    plt.xticks(rotation=25, ha="right", color=text_color)
    plt.yticks(color=text_color)

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(grid_color)
    ax.spines["left"].set_color(grid_color)

    plt.grid(True, axis="y", linestyle="--", color=grid_color, alpha=0.7)

    plt.title(
        "Asset Value Stacked Area Chart (100M KRW)",
        color="#FFFFFF",
        fontsize=13,
        fontweight="bold",
        pad=25,
    )
    plt.ylabel("Asset Value (100M KRW)", color=text_color, fontsize=10, labelpad=10)
    plt.xlabel("Date", color=text_color, fontsize=10, labelpad=10)

    legend = ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.15),  # 👈 1.02 -> 1.05로 올려 그래프 상단 곡선과의 간격 확보
        ncol=len(categories),  # 가로 정렬 유지
        frameon=False,
        fontsize=8.5,
    )
    for text in legend.get_texts():
        text.set_color(text_color)

    plt.tight_layout()
    plt.subplots_adjust(top=0.85)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor(), edgecolor="none")
    buf.seek(0)
    plt.close()

    return buf
