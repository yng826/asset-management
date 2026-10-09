"""bot/handlers/rebalance_handler.py - /target 목표 비중 조회·설정

사용법:
    /target                               현재 비중 vs 목표, 필요한 매수·매도 금액
    /target set 가상자산=25 해외ETF=35    목표 비중 설정 (일부만 지정 가능, 자산군 별칭 허용)
    /target clear 가상자산 | all          목표 삭제
    /target band 5                        허용 이탈폭(절대 %p) 변경
"""

import html
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from core.rebalance import (
    ASSET_CLASSES,
    clear_targets,
    get_rebalance_status,
    resolve_asset_class,
    set_band_pct,
    set_targets,
)

USAGE = (
    "⚖️ <b>목표 비중 설정</b>\n"
    "<code>/target set 가상자산=25 해외ETF=35 국내ETF=20</code>\n"
    "<code>/target clear 가상자산</code> · <code>/target clear all</code>\n"
    "<code>/target band 5</code> (허용 이탈폭 %p)\n\n"
    "자산군: " + ", ".join(ASSET_CLASSES) + "\n"
    "별칭: 코인, 국내주식, 국내ETF, 미국주식, 해외ETF, 펀드, 현금"
)


def build_status_text() -> str:
    status = get_rebalance_status()
    if not status["rows"]:
        return "📉 결산 스냅샷 데이터가 없습니다."

    lines = [
        f"⚖️ <b>목표 비중 현황</b> ({status['snapshot_date'][5:]} 결산, 총 {status['total_eval'] / 1e8:,.2f}억)",
        f"허용폭 ±{status['band_pct']:.1f}%p",
        "",
    ]
    has_target = False
    for row in status["rows"]:
        name = html.escape(row["asset_class"])
        if row["target_pct"] is None:
            lines.append(f"⚪ {name} {row['current_pct']:.1f}% (목표 없음)")
            continue
        has_target = True
        mark = ("🔺" if row["diff_pct"] > 0 else "🔻") if row["out_of_band"] else "🟢"
        amount = row["adjust_amount"]
        action = (
            f"{abs(amount) / 10_000:,.0f}만 {'매수' if amount > 0 else '매도'}"
            if abs(amount) >= 10_000
            else "조정 불필요"
        )
        lines.append(
            f"{mark} {name} {row['current_pct']:.1f}% / 목표 {row['target_pct']:.1f}% ({row['diff_pct']:+.1f}%p · {action})"
        )

    if has_target and abs(status["target_sum"] - 100) > 0.01:
        lines.append("")
        lines.append(f"⚠️ 목표 합계 {status['target_sum']:.1f}% (100%가 아님)")
    if not has_target:
        lines.append("")
        lines.append(USAGE)
    return "\n".join(lines)


def _parse_assignments(args: list[str]) -> tuple[dict[str, float], list[str]]:
    """['가상자산=25', '해외ETF', '35'] 형태 → ({자산군: 비중}, [오류 메시지])."""
    text = " ".join(args)
    targets, errors = {}, []
    for name, value in re.findall(r"([^\s=:]+(?:\s*(?:ETF|etf))?)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*%?", text):
        cls = resolve_asset_class(name)
        if cls is None:
            errors.append(f"알 수 없는 자산군: {html.escape(name)}")
            continue
        pct = float(value)
        if not 0 <= pct <= 100:
            errors.append(f"{cls}: 0~100 사이로 입력하세요")
            continue
        targets[cls] = pct
    return targets, errors


async def target_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.handlers.report_handler import _check_admin

    if not await _check_admin(update):
        return

    args = context.args or []
    sub = args[0].lower() if args else ""
    reply = update.effective_message.reply_text

    try:
        if sub == "set":
            targets, errors = _parse_assignments(args[1:])
            if targets:
                set_targets(targets)
            if errors or not targets:
                await reply(
                    "\n".join(errors or ["설정할 값이 없습니다."]) + "\n\n" + USAGE, parse_mode="HTML"
                )
                if not targets:
                    return
        elif sub == "clear":
            if len(args) > 1 and args[1].lower() == "all":
                clear_targets(None)
            else:
                classes = [resolve_asset_class(a) for a in args[1:]]
                if not classes or None in classes:
                    await reply("삭제할 자산군을 확인하세요.\n\n" + USAGE, parse_mode="HTML")
                    return
                clear_targets(classes)
        elif sub == "band":
            if len(args) < 2 or not re.fullmatch(r"\d+(\.\d+)?", args[1]) or not 0 < float(args[1]) <= 50:
                await reply("허용폭은 0~50 사이 숫자로 입력하세요. 예) /target band 5")
                return
            set_band_pct(float(args[1]))
        elif sub in ("help", "도움말"):
            await reply(USAGE, parse_mode="HTML")
            return

        await reply(build_status_text(), parse_mode="HTML")
        logging.info(f"✅ /target 명령어 응답 완료 ({sub or 'status'})")
    except Exception as e:
        logging.error(f"❌ /target 처리 실패: {e}", exc_info=True)
        await reply("⚠️ 목표 비중 처리 중 오류가 발생했습니다.")
