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

import datetime
import html

from core.calculator import (
    ACCOUNT_TYPE_ORDER,
    classify_account_type,
    get_closed_snapshot_date,
    group_holdings_by_account,
    summarize_accounts,
    summarize_total,
)
from database.repository import AssetRepository

# 텔레그램 4096자 한도 대비 + 한글 UTF-8 바이트 여유를 고려한 안전 마진
TG_MAX = 2400

# 최신 시세일 대비 이 일수 이상 오래된 시세는 "시세 지연" 경고 (주말·연휴 감안)
STALE_PRICE_DAYS = 4


# ----------------------------------------------------------------------
# 1. 손익 텍스트 빌더
# ----------------------------------------------------------------------
def format_pnl(profit: float, pnl_rate: float) -> str:
    """손익/수익률 텍스트 (🔺/🔹/➖ + 천단위 콤마 + 소수 둘째자리)."""
    if profit > 0:
        emoji, sign = "\U0001f53a", "+"  # 🔺
    elif profit < 0:
        emoji, sign = "🔹", ""  # 🔹
    else:
        emoji, sign = "\u2796", ""  # ➖
    return f"{emoji} {sign}{profit:,.0f}원 ({sign}{pnl_rate:.2f}%)"


def format_pnl_daily(pnl_data: list, provisional_date: str | None = None) -> str:
    """일별 손익 요약 포맷팅.

    - provisional_date: 아직 16:00 일일 결산 전인 당일 스냅샷 일자 (해당 행에 "(잠정)" 표기)
    """
    lines = ["📅 <b>최근 일자별 손익 요약</b>", "---------------------------------"]

    cumulative_pnl = 0
    for day in pnl_data:
        date = day["snapshot_date"]
        # YYYY-MM-DD -> MM-DD
        formatted_date = date[5:]
        pnl = day["daily_pnl"]
        pct = day["daily_return_pct"]

        if pnl > 0:
            sign = "+"
        elif pnl < 0:
            sign = ""
        else:
            sign = ""

        provisional_mark = " (잠정)" if date == provisional_date else ""
        lines.append(f"{formatted_date} | {sign}{pnl:,.0f}원 ({sign}{pct:.2f}%){provisional_mark}")
        cumulative_pnl += pnl

    lines.append("---------------------------------")

    # 누적 손익 처리
    sign = "+" if cumulative_pnl >= 0 else ""
    lines.append(f"기간 누적: {sign}{cumulative_pnl:,.0f}원")

    return "\n".join(lines)


def format_pnl_short(profit: float, pnl_rate: float) -> str:
    """한 페이지 요약용 짧은 손익 표기 (금액 + 수익률)."""
    if profit > 0:
        emoji, sign = "\U0001f53a", "+"
    elif profit < 0:
        emoji, sign = "🔹", ""
    else:
        emoji, sign = "\u2796", ""
    return f"{emoji} {sign}{profit:,.0f}원 ({sign}{pnl_rate:.2f}%)"


def format_asset_breakdown(summary_list: list[dict], total_eval: float) -> str:
    """자산군별 비중 리포트 텍스트 포맷팅"""
    if not summary_list:
        return "📊 <b>자산군별 비중</b>\n\n조회 가능한 데이터가 없습니다."

    date_str = datetime.date.today().strftime("%Y-%m-%d")
    lines = [f"📊 <b>자산군별 비중 리포트 ({date_str})</b>\n"]

    for row in summary_list:
        name = row["asset_class"]
        val = float(row["class_eval"])
        pct = float(row["weight_pct"])
        diff = float(row["eval_diff"])
        diff_pct = float(row["diff_pct"])

        # 억 / 만 단위 포맷팅
        if val >= 100_000_000:
            val_str = f"{val / 100_000_000:.2f}억"
        else:
            val_str = f"{val / 10_000:,.0f}만"

        # 변동액 포맷팅
        if diff > 0:
            emoji, sign = "\U0001f53a", "+"  # 🔺
        elif diff < 0:
            emoji, sign = "🔹", ""  # 🔹
        else:
            emoji, sign = "\u2796", ""  # ➖

        if abs(diff) >= 100_000_000:
            diff_str = f"{diff / 100_000_000:,.2f}억"
        else:
            diff_str = f"{diff / 10_000:,.0f}만"

        diff_line = f" | {emoji} {sign}{diff_str} ({sign}{diff_pct:.1f}%)"
        lines.append(f"• <b>{name}</b>: {val_str} ({pct:.1f}%) {diff_line}")

    # 하단 총액
    total_str = (
        f"{total_eval / 100_000_000:.2f}억 원" if total_eval >= 100_000_000 else f"{total_eval:,.0f}원"
    )
    lines.append("─────────────────────")
    lines.append(f"💰 <b>총 평가액</b>: {total_str}")
    lines.append("<i>(비중 1% 미만 소액 자산 제외)</i>")

    return "\n".join(lines)


def _format_eok_man(amount: float) -> str:
    """억 / 만 단위 축약 표기 (부호 유지)."""
    if abs(amount) >= 100_000_000:
        return f"{amount / 100_000_000:,.2f}억"
    return f"{amount / 10_000:,.0f}만"


def format_mmdd(date_str: str) -> str:
    """YYYY-MM-DD → MM/DD"""
    return str(date_str)[5:10].replace("-", "/")


def get_closed_pnl() -> dict | None:
    """최근 결산 확정일의 /pnl 행 (snapshot_date, daily_pnl, daily_return_pct). 없으면 None."""
    closed_date = get_closed_snapshot_date()
    if not closed_date:
        return None
    for row in AssetRepository().get_daily_pnl_history(closed_date, closed_date):
        return row
    return None


def format_asset_class_report(
    class_summaries: list[dict],
    header_lines: list | None = None,
    closed_pnl: dict | None = None,
) -> str:
    """자산군별 비중 리포트 (/breakdown).

    - class_summaries: core.calculator.summarize_asset_classes() 결과 (평가액 내림차순)
    - 비중 분모 = 표시된 자산군 평가액 합계 (= 총자산, 예수금 포함) → 비중 합계 100%
    - eval_diff / diff_pct: 최근 결산일의 자산군별 손익 (합계 = closed_pnl 의 daily_pnl)
    - header_lines: 기준 시세/환율 등 부가 라인
    - closed_pnl: get_closed_pnl() 결과 (결산일 표기 및 합계)
    """
    if not class_summaries:
        return "📊 <b>자산군별 비중</b>\n\n조회 가능한 데이터가 없습니다."

    lines = ["📊 <b>자산군별 비중 리포트</b>"]
    lines.extend(header_lines or [])
    lines.append("")

    for row in class_summaries:
        name = html.escape(row["asset_class"])
        diff = row["eval_diff"]
        if round(diff) > 0:
            emoji, sign = "\U0001f53a", "+"  # 🔺
        elif round(diff) < 0:
            emoji, sign = "🔹", ""  # 🔹
        else:
            emoji, sign = "➖", ""  # ➖
        diff_part = (
            f"  | {emoji} {sign}{_format_eok_man(diff)} ({sign}{row['diff_pct']:.1f}%)" if round(diff) else ""
        )
        lines.append(
            f"• <b>{name}</b>: {_format_eok_man(row['class_eval'])} ({row['weight_pct']:.1f}%){diff_part}"
        )

    total_eval = sum(r["class_eval"] for r in class_summaries)
    lines.append("─────────────────────")
    lines.append(f"💰 <b>총자산</b>: {total_eval:,.0f}원 (예수금 포함)")
    if closed_pnl:
        lines.append(
            f"📅 {format_mmdd(closed_pnl['snapshot_date'])} 결산 손익: "
            f"{format_pnl_short(closed_pnl['daily_pnl'], closed_pnl['daily_return_pct'])}"
        )
        lines.append("<i>(자산군별 손익은 결산일 기준, 장중 변동은 /live)</i>")

    return "\n".join(lines)


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
            "📊 <b>현재 보유 종목 현황</b>",
            "",
            "보유 중인 종목이 없습니다.",
            "",
            "<i>(데이터는 실제와 다를 수 있습니다.)</i>",
        ]

    grouped = group_holdings_by_account(enriched_holdings)
    account_summaries = summarize_accounts(grouped)
    total = summarize_total(enriched_holdings)

    lines: list = ["📊 <b>현재 보유 종목 현황</b>"]
    lines.extend(price_date_header_lines(enriched_holdings))
    lines.append("")

    # 1) 계좌별 그룹
    for acc_summary in account_summaries:
        account = html.escape(acc_summary["account_name"])
        lines.append(
            f"🏦 <b>{account}</b>  ·  {acc_summary['count']}개 종목  ·  "
            f"{format_pnl(acc_summary['profit'], acc_summary['pnl_rate'])}"
        )
        for h in grouped[account]:
            ticker = html.escape(h["ticker_name"])
            code = html.escape(h.get("ticker_code") or "-")
            qty = h["quantity"]
            avg_price = h["avg_price"]
            current_price = h["current_price"]
            valuation_amount = h["valuation_amount"]

            # 한 줄 압축 포맷: 수량 / 평단가 → 현재가 / 평가 / 손익
            if h["price_source"] == "us_stock":
                # 해외주식: $ 단가와 KRW 평가액을 모두 표기
                us_meta = h.get("_us") or {}
                usd_close = us_meta.get("usd_close", 0.0)
                krw_per_usd = us_meta.get("krw_per_usd", 0.0)
                fallback_mark = "  [해외·USD]"
                lines.append(
                    f"  • <code>{ticker} ({code})</code>{fallback_mark}\n"
                    f"    {qty:,.4f}주  평단 ${avg_price:,.2f}  "
                    f"→ 현재 ${usd_close:,.2f} (×{krw_per_usd:,.0f}원)  "
                    f"= 평가 {valuation_amount:,.0f}원  "
                    f"({format_pnl(h['profit'], h['pnl_rate'])})"
                )
                continue
            if h["price_source"] == "fund_nav":
                # 펀드: NAV/1000 좌당 기준가 → 평가액
                fallback_mark = "  [연금펀드·NAV]"
                lines.append(
                    f"  • <code>{ticker} ({code})</code>{fallback_mark}\n"
                    f"    {qty:,.4f}좌  평단 {avg_price:,.0f}원  "
                    f"→ NAV {current_price:,.2f} (1000좌당)  "
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
                f"  • <code>{ticker} ({code})</code>{fallback_mark}\n"
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
    lines.append("📈 <b>[전체 포트폴리오 요약]</b>")
    lines.append(f"• 종목 수: {total['count']}개")
    lines.append(f"• 총 매수금액: {total['buy_amount']:,.0f}원")
    lines.append(f"• 총 평가금액: {total['valuation_amount']:,.0f}원")
    lines.append(f"• 총 손익: {format_pnl(total['profit'], total['pnl_rate'])}")
    lines.append("")
    lines.append("<i>(데이터는 실제와 다를 수 있습니다.)</i>")

    return lines


# 내부 alias (과거 import 호환)
_render_lines = render_lines


# ----------------------------------------------------------------------
# 3. /status 한 페이지 요약 빌더


def price_date_header_lines(enriched_holdings: list, stale_days: int = STALE_PRICE_DAYS) -> list:
    """기준 시세 일자 범위 + 시세 미수집/지연 경고 라인 (/status, /details 공용).

    - 정기예금(일할 계산, 당일 기준)은 시세 일자 범위에서 제외
    - 시세 미수집: price_source == fallback
    - 시세 지연: 최신 시세일보다 stale_days 일 이상 오래된 종목
    """
    dated = [
        (str(it["price_date"])[:10], it)
        for it in enriched_holdings
        if it.get("price_date") and it.get("price_source") != "deposit"
    ]
    lines: list = []
    if dated:
        dates = sorted({d for d, _ in dated})
        date_range = dates[0] if len(dates) == 1 else f"{dates[0]} ~ {dates[-1]}"
        lines.append(f"기준 시세: {date_range}")

        latest = datetime.date.fromisoformat(dates[-1])
        stale = [it for d, it in dated if (latest - datetime.date.fromisoformat(d)).days >= stale_days]
        if stale:
            lines.append(f"⚠️ 시세 지연 {len(stale)}종목 ({stale_days}일 이상 미갱신)")

    fallback_count = sum(1 for it in enriched_holdings if it.get("price_source") == "fallback")
    if fallback_count:
        lines.append(f"⚠️ 시세 미수집 {fallback_count}종목 (평단가로 평가)")
    return lines


def build_status_summary(
    enriched_holdings: list,
    cash_by_account: dict | None = None,
    fx_rate: dict | None = None,
) -> str:
    """
    한 페이지 요약 리포트 문자열을 반환 (텔레그램 /status 용).

    - 모바일에서 한 화면에 들어오는 압축 포맷.
    - 계좌 유형(일반 / 연금·절세 / 가상자산)별로 묶고, 유형 내에서는 평가액 내림차순.
    - 각 계좌는 헤더 + (매수/평가 한 줄) + (손익·예수금 한 줄) = 3줄.
    - 최하단 [전체 포트폴리오 요약] 블록에 예수금 및 총자산(평가 + 예수금) 포함.
    - cash_by_account: {account_name: 원화 환산 예수금} (core.calculator.get_account_cash_map)
    - fx_rate: {price_date, rate} 해외 자산 환산에 사용한 환율 (헤더 표기용)
    - 빈 holdings 일 때는 안내 한 줄만 반환.
    """
    if not enriched_holdings:
        return "📊 <b>포트폴리오 한눈에 보기</b>\n\n보유 중인 종목이 없습니다.\n\n<i>(데이터는 실제와 다를 수 있습니다.)</i>"

    cash_by_account = cash_by_account or {}
    grouped = group_holdings_by_account(enriched_holdings)
    account_summaries = summarize_accounts(grouped)
    total = summarize_total(enriched_holdings)

    # 종목 없이 예수금만 있는 계좌도 표시
    held_accounts = {acc["account_name"] for acc in account_summaries}
    for account_name, cash in cash_by_account.items():
        if account_name not in held_accounts and round(cash) != 0:
            account_summaries.append(
                {
                    "account_name": account_name,
                    "buy_amount": 0.0,
                    "valuation_amount": 0.0,
                    "profit": 0.0,
                    "pnl_rate": 0.0,
                    "count": 0,
                }
            )

    lines: list = ["📊 <b>포트폴리오 한눈에 보기</b>"]
    lines.extend(price_date_header_lines(enriched_holdings))
    if fx_rate and fx_rate.get("rate"):
        lines.append(f"환율: {float(fx_rate['rate']):,.2f}원/USD ({fx_rate.get('price_date')})")
    lines.append("")

    # 계좌 유형별 그룹 (유형 헤더 1줄 + 계좌별 3줄)
    by_type: dict = {}
    for acc in account_summaries:
        by_type.setdefault(classify_account_type(acc["account_name"]), []).append(acc)

    for account_type in ACCOUNT_TYPE_ORDER:
        accounts = sorted(by_type.get(account_type, []), key=lambda a: a["valuation_amount"], reverse=True)
        if not accounts:
            continue
        type_eval = sum(a["valuation_amount"] + cash_by_account.get(a["account_name"], 0.0) for a in accounts)
        lines.append(f"📂 <b>{account_type}</b>  ·  {type_eval:,.0f}원")
        for acc in accounts:
            account_name = html.escape(acc["account_name"])
            cash = cash_by_account.get(acc["account_name"], 0.0)
            buy_amount = round(acc["buy_amount"])
            valuation_amount = round(acc["valuation_amount"])
            profit = valuation_amount - buy_amount
            lines.append(f"🏦 <b>{account_name}</b>  ({acc['count']}개 종목)")
            lines.append(f"   매수 {buy_amount:,.0f}원  /  평가 {valuation_amount:,.0f}원")
            pnl_line = f"   {format_pnl_short(profit, acc['pnl_rate'])}"
            if round(cash) != 0:
                pnl_line += f"  ·  예수금 {cash:,.0f}원"
            lines.append(pnl_line)
        lines.append("")

    # 전체 요약 블록 (최하단)
    cash_total = sum(cash_by_account.values())
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📈 <b>[전체 포트폴리오 요약]</b>")
    lines.append(f"   매수 {total['buy_amount']:,.0f}원  /  평가 {total['valuation_amount']:,.0f}원")
    lines.append(f"   {format_pnl_short(total['profit'], total['pnl_rate'])}")
    if cash_by_account:
        lines.append(f"   예수금 {cash_total:,.0f}원")
        lines.append(f"   💰 <b>총자산 {total['valuation_amount'] + cash_total:,.0f}원</b>")

    # 최근 결산일 손익 (/pnl 의 마지막 확정 행과 동일. 장중 변동은 /live)
    closed_pnl = get_closed_pnl()
    if closed_pnl:
        lines.append(
            f"   📅 {format_mmdd(closed_pnl['snapshot_date'])} 결산 손익: "
            f"{format_pnl_short(closed_pnl['daily_pnl'], closed_pnl['daily_return_pct'])}"
        )
    lines.append("")

    lines.append("<i>(데이터는 실제와 다를 수 있습니다.)</i>")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# 4. /details 청크 빌더 (다중 메시지 분할)
# ----------------------------------------------------------------------
def build_status_chunks(enriched_holdings: list, max_chars: int = TG_MAX) -> list:
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
