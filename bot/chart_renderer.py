import io

import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

matplotlib.use("Agg")

# 한글 폰트 후보 (Docker 이미지: fonts-nanum). 없으면 한글 라벨 대신 종목코드 사용
KOREAN_FONT_CANDIDATES = ("NanumGothic", "NanumBarunGothic", "Noto Sans CJK KR", "Malgun Gothic")


def _korean_font() -> str | None:
    """설치된 한글 폰트 이름 (없으면 None)."""
    from matplotlib import font_manager

    installed = {f.name for f in font_manager.fontManager.ttflist}
    return next((name for name in KOREAN_FONT_CANDIDATES if name in installed), None)


def _shade_estimated(ax, dates, label: bool = True) -> None:
    """실제 거래 기록 시작일(LEDGER_TRACKING_START) 이전 구간을 옅은 회색으로 표시 (9월 보유분 기준 추정치)."""
    from core.performance import LEDGER_TRACKING_START

    start, tracking = pd.Timestamp(dates[0]), pd.Timestamp(LEDGER_TRACKING_START)
    if start >= tracking:
        return
    end = min(tracking, pd.Timestamp(dates[-1]))
    ax.axvspan(start, end, color="gray", alpha=0.08, zorder=0)
    if label:
        ax.text(
            start,
            1.0,
            " estimated (holdings as of 09-01)",
            transform=ax.get_xaxis_transform(),
            fontsize=8,
            color="gray",
            va="bottom",
        )


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
    ax = plt.gca()
    ax.xaxis.set_major_locator(
        mdates.DayLocator(interval=3) if len(dates) <= 25 else mdates.AutoDateLocator(minticks=5, maxticks=8)
    )
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    plt.xticks(rotation=25, ha="right")

    plt.axhline(0, color="black", linestyle="-", linewidth=0.8, alpha=0.5)
    _shade_estimated(ax, dates)
    plt.title("Performance Comparison (Cumulative Return %)", fontsize=14, fontweight="bold", pad=14)
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

    return buf


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


def render_drawdown_chart(data: dict) -> io.BytesIO:
    """
    낙폭(Drawdown) 차트를 생성하여 메모리 버퍼로 반환.
    - 상단: 포트폴리오 TWR 누적 수익률, MDD 구간(고점→저점) 음영
    - 하단: 고점 대비 하락률 (포트폴리오 면적 + 벤치마크 점선)
    """
    name_map = {"KS11": "KOSPI", "KQ11": "KOSDAQ", "US500": "S&P 500", "IXIC": "NASDAQ", "KRW-BTC": "Bitcoin"}
    portfolio_color = "#1E293B"
    dd_color = "#DC2626"

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, (ax_top, ax_dd) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [1, 1.2]}
    )

    dates = pd.to_datetime(data["dates"])
    portfolio = data["portfolio"]
    metrics = portfolio["metrics"]

    # 1. 누적 수익률 + MDD 구간 음영
    ax_top.plot(
        dates, portfolio["cum_return"], color=portfolio_color, linewidth=2.5, label="My Portfolio (TWR)"
    )
    if metrics.get("mdd", 0) < 0:
        ax_top.axvspan(
            metrics["peak_date"], metrics["trough_date"], color=dd_color, alpha=0.08, label="Max Drawdown"
        )
    ax_top.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    _shade_estimated(ax_top, dates)
    ax_top.set_ylabel("Cumulative Return (%)", fontsize=11)
    ax_top.set_title("Drawdown & Risk", fontsize=14, fontweight="bold", pad=14)
    ax_top.legend(loc="upper left", frameon=True, fontsize=9)

    # 2. 고점 대비 하락률
    cmap = plt.get_cmap("tab10")
    for i, (ticker, bm) in enumerate(data["benchmarks"].items()):
        ax_dd.plot(
            dates,
            bm["drawdown"],
            linestyle="--",
            alpha=0.7,
            color=cmap(i),
            label=name_map.get(ticker, ticker),
        )
    ax_dd.fill_between(dates, portfolio["drawdown"], 0, color=dd_color, alpha=0.25)
    ax_dd.plot(dates, portfolio["drawdown"], color=dd_color, linewidth=1.8, label="My Portfolio")

    if metrics.get("mdd", 0) < 0:
        trough = metrics["trough_date"]
        ax_dd.scatter([trough], [metrics["mdd"]], color=dd_color, zorder=5)
        ax_dd.annotate(
            f"MDD {metrics['mdd']:.1f}%",
            (trough, metrics["mdd"]),
            textcoords="offset points",
            xytext=(8, -4),
            fontsize=10,
            fontweight="bold",
            color=dd_color,
        )

    _shade_estimated(ax_dd, dates, label=False)
    ax_dd.set_ylabel("Drawdown (%)", fontsize=11)
    ax_dd.legend(loc="lower left", frameon=True, fontsize=9)
    ax_dd.xaxis.set_major_locator(
        mdates.DayLocator(interval=3) if len(dates) <= 25 else mdates.AutoDateLocator(minticks=5, maxticks=8)
    )
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    plt.setp(ax_dd.get_xticklabels(), rotation=25, ha="right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    buf.seek(0)
    plt.close(fig)

    return buf


def render_monthly_pnl_chart(data: dict) -> io.BytesIO:
    """
    월별 투자손익 막대 차트 (입출금 제외)를 생성하여 메모리 버퍼로 반환.
    - 막대: 월 손익 (백만원, 이익 녹색 / 손실 적색), 막대 끝에 손익·월간 수익률 표기
    - 선: 누적 손익
    - 진행 중인 월은 라벨에 * 표시 + 빗금
    """
    from core.performance import has_estimated_period

    gain_color = "#16A34A"
    loss_color = "#DC2626"
    cum_color = "#1E293B"

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(10, 6))

    profit = [p / 1e6 for p in data["profit"]]
    cum = [c / 1e6 for c in data["cum_profit"]]
    labels = [
        pd.Period(m).strftime("%y-%b")
        + ("*" if partial else "")
        + ("\n(est)" if has_estimated_period(f"{m}-01") else "")
        for m, partial in zip(data["months"], data["partial"], strict=True)
    ]
    x = range(len(profit))

    bars = ax.bar(
        x,
        profit,
        color=[gain_color if p >= 0 else loss_color for p in profit],
        alpha=0.85,
        width=0.6,
        label="Monthly P&L",
    )
    for bar, partial, month in zip(bars, data["partial"], data["months"], strict=True):
        if partial:
            bar.set_hatch("//")
            bar.set_alpha(0.5)
        elif has_estimated_period(f"{month}-01"):
            bar.set_alpha(0.35)  # 09-01 이전: 9월 보유분 기준 추정치

    span = max(max(map(abs, profit + cum)), 1e-9)
    for i, (p, r) in enumerate(zip(profit, data["returns"], strict=True)):
        offset = span * 0.03
        ax.text(
            i,
            p + offset if p >= 0 else p - offset,
            f"{p:+.1f}M\n({r:+.1f}%)",
            ha="center",
            va="bottom" if p >= 0 else "top",
            fontsize=9,
            fontweight="bold",
            color=gain_color if p >= 0 else loss_color,
            zorder=5,
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 1},
        )

    ax.plot(x, cum, color=cum_color, marker="o", linewidth=2, label="Cumulative P&L")
    ax.text(
        len(cum) - 1,
        cum[-1],
        f"  {cum[-1]:+.1f}M",
        fontsize=10,
        fontweight="bold",
        color=cum_color,
        va="center",
    )

    ax.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylim(-span * 1.3, span * 1.3)
    ax.set_title("Monthly P&L (excl. deposits/withdrawals)", fontsize=14, fontweight="bold", pad=28)
    ax.set_ylabel("P&L (1M KRW)", fontsize=12)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, frameon=False, fontsize=10)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    buf.seek(0)
    plt.close(fig)

    return buf


def render_contribution_chart(data: dict, top_n: int = 8) -> io.BytesIO:
    """
    종목별 수익 기여도 가로 막대 차트를 생성하여 메모리 버퍼로 반환.
    - 상위 이익 top_n + 하위 손실 top_n 종목, 나머지는 "기타 N종목"으로 합산
    - 한글 폰트가 없으면 종목명 대신 종목코드 표시
    """
    gain_color = "#16A34A"
    loss_color = "#DC2626"

    plt.style.use("seaborn-v0_8-whitegrid")
    font = _korean_font()
    if font:
        plt.rcParams["font.family"] = [font, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

    items = data["items"]
    gains = [it for it in items if it["profit"] > 0][:top_n]
    losses = [it for it in items if it["profit"] < 0][-top_n:]
    shown = gains + losses
    rest = [it for it in items if it not in shown and it["profit"] != 0]
    if rest:
        shown.append(
            {"ticker": "ETC", "name": f"기타 {len(rest)}종목", "profit": sum(it["profit"] for it in rest)}
        )
        shown.sort(key=lambda it: it["profit"], reverse=True)

    def label(it: dict) -> str:
        if not font:
            return it["ticker"] if it["ticker"] != "ETC" else f"Others ({len(rest)})"
        name = it["name"]
        return name if len(name) <= 16 else name[:15] + "…"

    labels = [label(it) for it in shown][::-1]  # 위에서부터 이익 큰 순
    values = [it["profit"] / 1e6 for it in shown][::-1]
    base = data["base_eval"] or 1.0

    fig, ax = plt.subplots(figsize=(10, max(4.5, 0.42 * len(values) + 1.5)))
    y = range(len(values))
    ax.barh(y, values, color=[gain_color if v >= 0 else loss_color for v in values], alpha=0.85, height=0.65)

    span = max(map(abs, values)) or 1.0
    for i, v in enumerate(values):
        ax.text(
            v + (span * 0.02 if v >= 0 else -span * 0.02),
            i,
            f"{v:+.2f}M ({v * 1e6 / base * 100:+.2f}%p)",
            va="center",
            ha="left" if v >= 0 else "right",
            fontsize=9,
            color=gain_color if v >= 0 else loss_color,
        )

    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=10)
    ax.set_xlim(-span * 1.6, span * 1.6)
    ax.axvline(0, color="black", linewidth=0.8, alpha=0.6)
    total = data["total_profit"] / 1e6
    ax.set_title(
        f"P&L Contribution by Holding  {data['start'][5:]} ~ {data['end'][5:]}  (Total {total:+.1f}M)",
        fontsize=13,
        fontweight="bold",
    )
    ax.set_xlabel("P&L (1M KRW)  /  %p of starting value", fontsize=11)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    buf.seek(0)
    plt.close(fig)

    return buf


def render_fx_attribution_chart(data: dict) -> io.BytesIO:
    """
    달러 자산 환율 효과 분리 차트를 생성하여 메모리 버퍼로 반환.
    - 상단: 달러 자산 묶음 누적 수익률 (원화 기준 / 달러 기준) + USD/KRW 변동률
    - 하단: 종목별 주가 효과 / 환율 효과 묶음 가로 막대 (원화)
    """
    krw_color = "#1E293B"
    usd_color = "#2563EB"
    fx_color = "#D97706"

    plt.style.use("seaborn-v0_8-whitegrid")
    font = _korean_font()
    if font:
        plt.rcParams["font.family"] = [font, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

    items = data["items"]
    fig, (ax_top, ax_bar) = plt.subplots(
        2,
        1,
        figsize=(10, 6 + 0.45 * len(items)),
        gridspec_kw={"height_ratios": [1, max(1.0, 0.25 * len(items))]},
    )

    # 1. 누적 수익률 비교
    dates = pd.to_datetime(data["dates"])
    series = [
        (data["cum_krw"], "USD assets in KRW", krw_color, "-", 2.5),
        (data["cum_usd"], "USD assets in USD", usd_color, "--", 1.8),
        (data["cum_fx"], "USD/KRW", fx_color, ":", 1.8),
    ]
    for values, label, color, style, width in series:
        ax_top.plot(dates, values, label=label, color=color, linestyle=style, linewidth=width)
        ax_top.text(dates[-1], values[-1], f" {values[-1]:+.1f}%", color=color, fontsize=9, va="center")
    ax_top.axhline(0, color="black", linewidth=0.8, alpha=0.5)
    _shade_estimated(ax_top, dates)
    ax_top.set_ylabel("Cumulative (%)", fontsize=11)
    ax_top.set_title(
        f"FX Effect on USD Assets  {data['start'][5:]} ~ {data['end'][5:]}", fontsize=14, fontweight="bold"
    )
    ax_top.legend(loc="best", frameon=True, fontsize=9)
    ax_top.xaxis.set_major_locator(
        mdates.DayLocator(interval=3) if len(dates) <= 25 else mdates.AutoDateLocator(minticks=5, maxticks=8)
    )
    ax_top.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))

    # 2. 종목별 주가 / 환율 효과
    labels = [it["name"] if font else it["ticker"] for it in items][::-1]
    price = [it["price_effect"] / 1e4 for it in items][::-1]
    fx = [it["fx_effect"] / 1e4 for it in items][::-1]
    y = list(range(len(items)))
    h = 0.38
    ax_bar.barh([v + h / 2 for v in y], price, height=h, color=usd_color, alpha=0.85, label="Price effect")
    ax_bar.barh([v - h / 2 for v in y], fx, height=h, color=fx_color, alpha=0.85, label="FX effect")
    for i in y:
        total = price[i] + fx[i]
        ax_bar.text(
            max(price[i], fx[i], 0),
            i,
            f"  = {total:+,.0f}",
            va="center",
            fontsize=9,
            fontweight="bold",
            color="#16A34A" if total >= 0 else "#DC2626",
        )
    span = max([abs(v) for v in price + fx] or [1.0])
    ax_bar.set_xlim(-span * 1.25, span * 1.45)
    ax_bar.set_yticks(y)
    ax_bar.set_yticklabels(labels, fontsize=10)
    ax_bar.axvline(0, color="black", linewidth=0.8, alpha=0.6)
    ax_bar.set_xlabel("P&L (10K KRW)", fontsize=11)
    ax_bar.legend(loc="lower right", frameon=True, fontsize=9)
    ax_bar.grid(axis="y", visible=False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    buf.seek(0)
    plt.close(fig)

    return buf


def render_dividend_chart(data: dict, top_n: int = 6) -> io.BytesIO:
    """
    연간 월별 배당 차트를 생성하여 메모리 버퍼로 반환.
    - 막대: 월별 배당 (종목별 누적, 상위 top_n 외 '기타'), 단위 만원
    - 선: 연간 누적 배당 (보조축), 전년 월별 배당이 있으면 회색 점선 표시
    """
    plt.style.use("seaborn-v0_8-whitegrid")
    font = _korean_font()
    if font:
        plt.rcParams["font.family"] = [font, "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False

    months = list(range(1, 13))
    by_ticker = data["monthly_by_ticker"]
    ranked = sorted(by_ticker, key=lambda name: sum(by_ticker[name]), reverse=True)
    stacks = [(name, by_ticker[name]) for name in ranked[:top_n]]
    if len(ranked) > top_n:
        others = [sum(by_ticker[name][i] for name in ranked[top_n:]) for i in range(12)]
        stacks.append((f"기타 {len(ranked) - top_n}" if font else f"Others ({len(ranked) - top_n})", others))

    fig, ax = plt.subplots(figsize=(10, 6))
    cmap = plt.get_cmap("tab10")
    bottom = [0.0] * 12
    for i, (name, values) in enumerate(stacks):
        values = [v / 1e4 for v in values]
        label = name if font else data["codes"].get(name, name)
        ax.bar(months, values, bottom=bottom, color=cmap(i), alpha=0.85, width=0.65, label=label)
        bottom = [b + v for b, v in zip(bottom, values, strict=True)]

    for m, total in zip(months, bottom, strict=True):
        if total > 0:
            ax.text(
                m,
                total,
                f"{total:,.1f}",
                ha="center",
                va="bottom",
                fontsize=9,
                fontweight="bold",
                zorder=5,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 1},
            )

    if data.get("prev_monthly"):
        prev = [v / 1e4 for v in data["prev_monthly"]]
        ax.plot(months, prev, color="gray", linestyle=":", marker="o", alpha=0.7, label=f"{data['year'] - 1}")

    ax2 = ax.twinx()
    cumulative, running = [], 0.0
    for v in data["monthly"][: data["last_month"]]:  # 올해는 이번 달까지만
        running += v / 1e4
        cumulative.append(running)
    ax2.plot(
        months[: len(cumulative)],
        cumulative,
        color="#1E293B",
        linewidth=2,
        marker="o",
        markersize=4,
        label="Cumulative",
    )
    ax2.set_ylim(0, max(cumulative[-1] * 1.15, 1))
    ax2.set_ylabel("Cumulative (10K KRW)", fontsize=11)
    ax2.grid(False)
    ax.set_zorder(ax2.get_zorder() + 1)  # 막대 라벨이 누적선 위에 오도록
    ax.patch.set_visible(False)

    ax.set_xticks(months)
    ax.set_xticklabels([pd.Timestamp(2000, m, 1).strftime("%b") for m in months])
    ax.set_ylim(0, max(max(bottom) * 1.25, 1))
    ax.set_ylabel("Monthly Dividends (10K KRW)", fontsize=11)
    ax.set_title(
        f"Dividends {data['year']}  (Total {data['total'] / 1e4:,.1f} x 10K KRW)",
        fontsize=14,
        fontweight="bold",
    )
    handles, labels = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(handles + h2, labels + l2, loc="upper left", frameon=True, fontsize=9)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=100)
    buf.seek(0)
    plt.close(fig)

    return buf
