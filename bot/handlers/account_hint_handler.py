"""bot/handlers/account_hint_handler.py - 증권사 알림의 계좌번호 → 계좌 매핑 (callback_data 접두어 'ah:')

- 알림 파싱 중 계좌번호를 모르는 건은 계좌 선택 버튼으로 묻고, 선택하면 번호를 기억한 뒤 그 건들을 기록
- /hint            등록된 매핑 목록
- /hint add 한투 IRP 64****89-29   수동 등록 (마지막 토큰이 알림에 보이는 번호)
- /hint del 3      번호(목록의 id)로 삭제
"""

import asyncio
import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.report_handler import _check_admin, _safe_edit
from config.constants import ACCOUNT_ALIASES, ASSET_MAP
from core.account_hints import normalize_pattern
from database.repository import AssetRepository

ACCOUNTS = list(ASSET_MAP.keys())
ACTION_KR = {"BUY": "매수", "SELL": "매도", "DIVIDEND": "배당", "DEPOSIT": "입금", "WITHDRAW": "출금"}
PENDING_KEY = "ah_pending"

USAGE = "<code>/hint add 한투 IRP 64****89-29</code> · <code>/hint del 3</code>"


def describe_transaction(parsed: dict) -> str:
    """저장 결과·확인용 한 줄 (HTML)."""
    currency = parsed.get("currency") or "KRW"
    unit = "$" if currency == "USD" else "원"
    action = ACTION_KR.get(parsed.get("action_type"), parsed.get("action_type"))
    total = parsed.get("total_amount") or 0.0
    return (
        f"• [{action}] {html.escape(str(parsed.get('account_name')))} | "
        f"{html.escape(str(parsed.get('ticker_name')))} | {total:,.2f}{unit} ({parsed.get('trans_date')})"
    )


def split_unresolved(parsed_list: list[dict]) -> tuple[list[dict], list[dict]]:
    """(바로 기록할 건, 계좌번호를 몰라 계좌를 물어야 하는 건)."""
    ready = [p for p in parsed_list if not p.get("unresolved_hint")]
    pending = [p for p in parsed_list if p.get("unresolved_hint")]
    return ready, pending


async def ask_account(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: list[dict], memo: str
) -> None:
    """번호별로 계좌 선택 버튼 메시지를 보낸다. 대기 건은 user_data 에 두고 콜백에서 꺼낸다."""
    store = context.user_data.setdefault(PENDING_KEY, {})
    by_pattern: dict[str, list[dict]] = {}
    for item in pending:
        by_pattern.setdefault(item["unresolved_hint"], []).append(item)

    for pattern, items in by_pattern.items():
        seq = context.user_data["ah_seq"] = (
            context.user_data.get("ah_seq", 0) + 1
        )  # 지난 버튼과 토큰이 겹치지 않게 증가만
        token = str(seq)
        store[token] = {"pattern": pattern, "items": items, "memo": memo}
        rows = [
            [
                InlineKeyboardButton(name, callback_data=f"ah:{token}:{i}")
                for i, name in enumerate(ACCOUNTS[j : j + 2], j)
            ]
            for j in range(0, len(ACCOUNTS), 2)
        ]
        rows.append([InlineKeyboardButton("❌ 기록 안 함", callback_data=f"ah:{token}:x")])
        text = (
            f"🔎 계좌번호 <code>{html.escape(pattern)}</code> 가 어느 계좌인지 모릅니다.\n"
            f"선택하면 번호를 기억해 두고 아래 {len(items)}건을 기록합니다.\n\n"
            + "\n".join(describe_transaction(i) for i in items)
        )
        await update.effective_message.reply_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
        )


async def ah_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """계좌 선택 버튼: ah:<token>:<계좌 순번|x>"""
    query = update.callback_query
    await query.answer()
    if not await _check_admin(update):
        return

    _, token, choice = query.data.split(":")
    entry = context.user_data.get(PENDING_KEY, {}).pop(token, None)
    if entry is None:
        await _safe_edit(query, "⚠️ 만료된 버튼입니다. 알림을 다시 보내 주세요.")
        return
    if choice == "x":
        await _safe_edit(query, "취소했습니다. 기록하지 않았습니다.")
        return

    account = ACCOUNTS[int(choice)]
    repo = AssetRepository()
    await asyncio.to_thread(repo.add_account_hint, account, entry["pattern"])

    lines = []
    for item in entry["items"]:
        item.pop("unresolved_hint", None)
        item["account_name"] = account
        dup = await asyncio.to_thread(repo.has_same_transaction, item)
        if await asyncio.to_thread(repo.add_transaction, item, entry["memo"]):
            lines.append(describe_transaction(item) + (" ⚠️ 동일 내역이 이미 있었음" if dup else ""))
    logging.info(f"✅ 계좌번호 {entry['pattern']} → {account} 등록, {len(lines)}건 기록")
    await _safe_edit(
        query,
        f"✅ <code>{html.escape(entry['pattern'])}</code> = <b>{html.escape(account)}</b> 로 기억했습니다.\n"
        f"{len(lines)}건 기록 완료\n" + "\n".join(lines),
    )


async def hint_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/hint — 계좌번호 매핑 목록·등록·삭제."""
    if not await _check_admin(update):
        return
    repo = AssetRepository()
    args = context.args or []

    if args and args[0] == "del" and len(args) == 2 and args[1].isdigit():
        ok = await asyncio.to_thread(repo.delete_account_hint, int(args[1]))
        await update.effective_message.reply_text("🗑 삭제했습니다." if ok else "⚠️ 삭제하지 못했습니다.")
        return

    if args and args[0] == "add":
        pattern = normalize_pattern(args[-1]) if len(args) >= 3 else None
        name = " ".join(args[1:-1])
        account = ACCOUNT_ALIASES.get(name, name)
        if not pattern or account not in ASSET_MAP:
            await update.effective_message.reply_text(
                f"⚠️ 계좌명 또는 번호 형식이 올바르지 않습니다.\n{USAGE}\n계좌: {', '.join(ACCOUNTS)}",
                parse_mode="HTML",
            )
            return
        ok = await asyncio.to_thread(repo.add_account_hint, account, pattern)
        await update.effective_message.reply_text(
            f"✅ {pattern} = {account}" if ok else "⚠️ 저장하지 못했습니다."
        )
        return

    rows = await asyncio.to_thread(repo.list_account_hints)
    body = "\n".join(
        f"{i}. {html.escape(account)} — <code>{html.escape(hint)}</code>" for i, account, hint in rows
    )
    await update.effective_message.reply_text(
        "📒 <b>등록된 계좌번호</b>\n" + (body or "(없음)") + f"\n\n{USAGE}", parse_mode="HTML"
    )
