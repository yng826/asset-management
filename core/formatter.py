"""
core/formatter.py
- 텔레그램 메시지용 문자열/청크 빌더만 담당.
- 입력: enrich_holdings_with_prices()가 만든 dict 리스트 (불변 데이터).
- 출력: 텔레그램 전송용 str / list[str] (4096자 한도 내).

순수 포맷팅 모듈:
  - DB 조회, 가격 매핑, 평가 산출 같은 사이드 이펙트 일체 없음.
  - 집계는 core.calculator.summarize_* / group_* 위임.
  - 청크 분할/이모지/숫자 포맷팅은 자체 처리.

calculator와의 인터페이스 (느슨한 결합):
  enriched_holdings dict 필수 키:
    - ticker_name, ticker_code, account_name, quantity, avg_price
    - current_price, price_date, price_source, buy_amount
    - valuation_amount, profit, pnl_rate
    - _deposit (정기예금 메타, 선택)
    - _us (해외주식 메타, 선택)
"""

from collections import OrderedDict
from typing import Optional

from core.calculator import (
    group_holdings_by_account,
    summarize_accounts,
    summarize_total,
)


# 텔레그램 4096자 한도 대비 + 한글 UTF-8 바이트 여유를 고려한 안전 마진
TG_MAX = 2400


# ----------------------------------------------------------------------
# 1. 손익 텍스트 빌더
# ----------------------------------------------------------------------
def format_pnl(profit: float, pnl_rate: float) -> str:
    """손익/수익률 텍스트 (🔺/🔻/➖ + 천단위 콤마 + 소수 둘째자리)."""
    if profit > 0:
        emoji, sign = "\U0001f53a", "+"  # 🔺
    elif profit < 0:
        emoji, sign = "\U0001f53b", ""   # 🔻
    else:
        emoji, sign = "\u2796", ""       # ➖
    return f"{emoji} {sign}{profit:,.0f}원 ({sign}{pnl_rate:.2f}%)"


def format_pnl_short(profit: float, pnl_rate: float) -> str:
    """한 페이지 요약용 짧은 손익 표기 (금액 + 수익률)."""
    if profit > 0:
        emoji, sign = "\U0001f53a", "+"
    elif profit < 0:
        emoji, sign = "\U0001f53b", ""
    else:
        emoji, sign = "\u2796", ""
    return f"{emoji} {sign}{profit:,.0f}원 ({sign}{pnl_rate:.2f}%)"


# 내부 alias (과거 import 호환)
_format_pnl = format_pnl
_format_pnl_short = format_pnl_short


# ----------------------------------------------------------------------
# 2. 라인 빌더 (계좌 그룹 + 종목 + 전체 요약)
# ----------------------------------------------------------------------
def render_lines(enriched_holdings: list) -> list:
    """
    텔레그램 메시지용 라인 리스트를 생성.
    - /details(build_status_chunks)와 /status(build_status_summary) 양쪽에서 공유.
    - 출력 줄 단위 구조는 텔레그램 모바일 가독성에 최적화.
    """
    if not enriched_holdings:
        return [
            "📊 현재 보유 종목 현황",
            "",
            "보유 중인 종목이 없습니다.",
            "",
            "(데이터는 실제와 다를 수 있습니다.)",
        ]

    grouped = group_holdings_by_account(enriched_holdings)
    account_summaries = summarize_accounts(grouped)
    total = summarize_total(enriched_holdings)

    # 최신 price_date 헤더 표기 (있을 때만)
    latest_price_date = None
    for it in enriched_holdings:
        if it.get("price_date"):
            latest_price_date = it["price_date"]
            break

    lines: list = ["📊 현재 보유 종목 현황"]
    if latest_price_date:
        lines.append(f"기준 시세: {latest_price_date}")
    lines.append("")

    # 1) 계좌별 그룹
    for acc_summary in account_summaries:
        account = acc_summary["account_name"]
        lines.append(
            f"🏦 {account}  ·  {acc_summary['count']}개 종목  ·  "
            f"{format_pnl(acc_summary['profit'], acc_summary['pnl_rate'])}"
        )
        for h in grouped[account]:
            ticker = h["ticker_name"]
            code = h.get("ticker_code") or "-"
            qty = h["quantity"]
            avg_price = h["avg_price"]
            current_price = h["current_price"]
            buy_amount = h["buy_amount"]
            valuation_amount = h["valuation_amount"]

            # 한 줄 압축 포맷: 수량 / 평단가 → 현재가 / 평가 / 손익
            if h["price_source"] == "us_stock":
                # 해외주식: $ 단가와 KRW 평가액을 모두 표기
                us_meta = h.get("_us") or {}
                usd_close = us_meta.get("usd_close", 0.0)
                krw_per_usd = us_meta.get("krw_per_usd", 0.0)
                fallback_mark = "  [해외·USD]"
                lines.append(
                    f"  • {ticker} ({code}){fallback_mark}\n"
                    f"    {qty:,.4f}주  평단 ${avg_price:,.2f}  "
                    f"→ 현재 ${usd_close:,.2f} (×{krw_per_usd:,.0f}원)  "
                    f"= 평가 {valuation_amount:,.0f}원  "
                    f"({format_pnl(h['profit'], h['pnl_rate'])})"
                )
                continue
            if h["price_source"] == "deposit":
                fallback_mark = "  [정기예금·일할]"
            elif h["price_source"] == "fallback":
                fallback_mark = "  [예수금/미수집]"
            else:
                fallback_mark = ""
            lines.append(
                f"  • {ticker} ({code}){fallback_mark}\n"
                f"    {qty:,.4f}주  평단 {avg_price:,.0f}원  "
                f"→ 현재 {current_price:,.0f}원  "
                f"= 평가 {valuation_amount:,.0f}원  "
                f"({format_pnl(h['profit'], h['pnl_rate'])})"
            )
        # 계좌 소계 (한 줄)
        lines.append(
            f"  └ 소계: 매수 {acc_summary['buy_amount']:,.0f}원  /  "
            f"평가 {acc_summary['valuation_amount']:,.0f}원  ·  "
            f"{format_pnl(acc_summary['profit'], acc_summary['pnl_rate'])}"
        )
        lines.append("")

    # 2) 전체 요약 (반드시 마지막)
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📈 [전체 포트폴리오 요약]")
    lines.append(f"• 종목 수: {total['count']}개")
    lines.append(f"• 총 매수금액: {total['buy_amount']:,.0f}원")
    lines.append(f"• 총 평가금액: {total['valuation_amount']:,.0f}원")
    lines.append(f"• 총 손익: {format_pnl(total['profit'], total['pnl_rate'])}")
    lines.append("")
    lines.append("(데이터는 실제와 다를 수 있습니다.)")

    return lines


# 내부 alias (과거 import 호환)
_render_lines = render_lines


# ----------------------------------------------------------------------
# 3. /status 한 페이지 요약 빌더
# ----------------------------------------------------------------------
def build_status_summary(enriched_holdings: list) -> str:
    """
    한 페이지 요약 리포트 문자열을 반환 (텔레그램 /status 용).

    - 모바일에서 한 화면에 들어오는 압축 포맷.
    - 각 계좌는 헤더 + (매수/평가 한 줄) + (손익 한 줄) = 약 3줄.
    - 최하단 [전체 포트폴리오 요약] 블록 포함.
    - 텔레그램 4,096자 한도 내 단일 메시지 목표 (보통 1,500자 이내).
    - 빈 holdings 일 때는 안내 한 줄만 반환.
    """
    if not enriched_holdings:
        return (
            "📊 포트폴리오 한눈에 보기\n\n"
            "보유 중인 종목이 없습니다.\n\n"
            "(데이터는 실제와 다를 수 있습니다.)"
        )

    grouped = group_holdings_by_account(enriched_holdings)
    account_summaries = summarize_accounts(grouped)
    total = summarize_total(enriched_holdings)

    # 최신 price_date 헤더 표기
    latest_price_date = None
    for it in enriched_holdings:
        if it.get("price_date"):
            latest_price_date = it["price_date"]
            break

    lines: list = ["📊 포트폴리오 한눈에 보기"]
    if latest_price_date:
        lines.append(f"기준 시세: {latest_price_date}")
    lines.append("")

    # 계좌별 (헤더 1줄 + 매수/평가 1줄 + 손익 1줄 = 3줄)
    for acc in account_summaries:
        lines.append(
            f"🏦 {acc['account_name']}  ({acc['count']}개 종목)"
        )
        lines.append(
            f"   매수 {acc['buy_amount']:,.0f}원  /  "
            f"평가 {acc['valuation_amount']:,.0f}원"
        )
        lines.append(
            f"   {format_pnl_short(acc['profit'], acc['pnl_rate'])}"
        )

    # 전체 요약 블록 (최하단)
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📈 [전체 포트폴리오 요약]")
    lines.append(
        f"   매수 {total['buy_amount']:,.0f}원  /  "
        f"평가 {total['valuation_amount']:,.0f}원"
    )
    lines.append(
        f"   {format_pnl_short(total['profit'], total['pnl_rate'])}"
    )
    lines.append("")
    lines.append("(데이터는 실제와 다를 수 있습니다.)")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 4. /details 청크 빌더 (다중 메시지 분할)
# ----------------------------------------------------------------------
def build_status_chunks(enriched_holdings: list,
                        max_chars: int = TG_MAX) -> list:
    """
    텔레그램 분할 전송용 list[str] 반환.
    - 라인 단위로 안전하게 누적하다가 다음 라인이 한도를 넘으면 새 청크로 분할.
    - 전체 요약 블록은 절대 잘리지 않도록 마지막 청크에 반드시 포함.
    """
    if not enriched_holdings:
        return ["\n".join(render_lines(enriched_holdings))]

    lines = render_lines(enriched_holdings)

    # 빈 holdings 케이스: 전체를 한 청크로 반환
    if "보유 중인 종목이 없습니다." in lines[2]:
        return ["\n".join(lines)]

    # 마지막 "전체 요약" 블록 식별 (━━━━━━━━━━━━━━━ 부터 끝까지)
    summary_start_idx = None
    for i, line in enumerate(lines):
        if line.startswith("━━━━━━━━━━━━━━━"):
            summary_start_idx = i
            break
    if summary_start_idx is None:
        # 방어: 요약 블록이 없으면 그냥 마지막 4줄을 요약으로 간주
        summary_start_idx = max(0, len(lines) - 4)

    summary_lines = lines[summary_start_idx:]
    head_lines = lines[:summary_start_idx]

    # 청크 분할: 헤더 라인을 가능한 한 max_chars 안에서 누적
    chunks: list = []
    cur_chunk: list = []
    cur_len = 0

    def flush():
            nonlocal cur_chunk, cur_len
            if cur_chunk:
                chunks.append("\n".join(cur_chunk))
                cur_chunk = []
                cur_len = 0

    for line in head_lines:
        line_len = len(line) + 1  # 줄바꿈 포함
        # 단일 라인이 max_chars보다 길면 그대로 flush 후 단독 청크로
        if line_len > max_chars:
            flush()
            chunks.append(line)
            continue
        if cur_len + line_len > max_chars:
            flush()
        cur_chunk.append(line)
        cur_len += line_len
    flush()

    # 요약 블록은 항상 마지막 청크에 결합 (가능하면 직전 청크에 합쳐 1메시지 처리)
    summary_text = "\n".join(summary_lines)
    if chunks and (len(chunks[-1]) + 1 + len(summary_text)) <= max_chars:
        chunks[-1] = chunks[-1] + "\n" + summary_text
    else:
        chunks.append(summary_text)

    return chunks


def build_status_report(enriched_holdings: list) -> str:
    """
    단일 문자열로 받고 싶은 경우(테스트/CLI) 위한 헬퍼.
    실제 텔레그램 전송은 build_status_chunks()를 사용해 분할하는 것을 권장.
    """
    return "\n\n".join(build_status_chunks(enriched_holdings))


# ----------------------------------------------------------------------
# 5. CLI 진입점 (수동 실행 / 테스트용)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # CLI 단독 사용 시: calculator가 보유 조회를 담당하므로 의존성 역전 회피
    from core.calculator import enrich_holdings_with_prices, get_latest_fx_rate, get_latest_prices_map
    from database.repository import AssetRepository

    repo = AssetRepository()
    holdings = repo.get_current_holdings()
    price_map = get_latest_prices_map()
    fx_rate = get_latest_fx_rate()
    enriched = enrich_holdings_with_prices(holdings, price_map, fx_rate)

    print(build_status_summary(enriched))
