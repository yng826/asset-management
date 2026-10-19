"""bot/handlers/account_hint_handler.py - 증권사 알림의 계좌번호 → 계좌 매핑 (callback_data 접두어 'ah:')

- 알림 파싱 중 계좌를 모르는 건(번호가 처음이거나, 계좌 단서가 없는 건)은 계좌 선택 버튼으로 묻고, 선택하면 그 건들을 기록
  (번호가 있으면 함께 기억). 증권사 이름 단서가 있으면 그 증권사 계좌만, 없거나 안 맞으면 전체 계좌를 보여 준다
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
from core.account_hints import candidate_accounts, normalize_pattern
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
        f"• [{action}] {html.escape(str(parsed.get('account_name') or '계좌 미정'))} | "
        f"{html.escape(str(parsed.get('ticker_name')))} | {total:,.2f}{unit} ({parsed.get('trans_date')})"
    )


def _needs_account(parsed: dict) -> bool:
    return bool(parsed.get("unresolved_hint")) or parsed.get("account_name") not in ASSET_MAP


def split_unresolved(parsed_list: list[dict]) -> tuple[list[dict], list[dict]]:
    """(바로 기록할 건, 계좌를 몰라 계좌를 물어야 하는 건)."""
    ready = [p for p in parsed_list if not _needs_account(p)]
    pending = [p for p in parsed_list if _needs_account(p) and p.get("action_type")]
    return ready, pending


def _account_keyboard(token: str, accounts: list[str], narrowed: bool) -> InlineKeyboardMarkup:
    """accounts 의 전체 계좌 순번(ACCOUNTS 기준)으로 버튼을 만든다. 좁힌 목록이면 '전체 계좌 보기' 버튼을 붙인다."""
    indexed = [(ACCOUNTS.index(name), name) for name in accounts]
    rows = [
        [InlineKeyboardButton(name, callback_data=f"ah:{token}:{i}") for i, name in indexed[j : j + 2]]
        for j in range(0, len(indexed), 2)
    ]
    if narrowed:
        rows.append([InlineKeyboardButton("🔽 전체 계좌 보기", callback_data=f"ah:{token}:a")])
    rows.append([InlineKeyboardButton("❌ 기록 안 함", callback_data=f"ah:{token}:x")])
    return InlineKeyboardMarkup(rows)


async def ask_account(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: list[dict], memo: str
) -> None:
    """(계좌번호, 증권사 단서)별로 계좌 선택 버튼 메시지를 보낸다. 대기 건은 user_data 에 두고 콜백에서 꺼낸다."""
    store = context.user_data.setdefault(PENDING_KEY, {})
    groups: dict[tuple[str, str], list[dict]] = {}
    for item in pending:
        groups.setdefault((item.get("unresolved_hint") or "", item.get("broker_hint") or ""), []).append(item)

    for (pattern, broker), items in groups.items():
        seq = context.user_data["ah_seq"] = (
            context.user_data.get("ah_seq", 0) + 1
        )  # 지난 버튼과 토큰이 겹치지 않게 증가만
        token = str(seq)
        store[token] = {"pattern": pattern, "items": items, "memo": memo}
        candidates = candidate_accounts(broker)
        if pattern:
            head = f"🔎 계좌번호 <code>{html.escape(pattern)}</code> 가 어느 계좌인지 모릅니다.\n선택하면 번호를 기억해 두고"
        else:
            head = "🔎 어느 계좌의 거래인지 알 수 없습니다.\n선택하면"
        text = f"{head} 아래 {len(items)}건을 기록합니다.\n\n" + "\n".join(
            describe_transaction(i) for i in items
        )
        await update.effective_message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=_account_keyboard(token, candidates, narrowed=len(candidates) < len(ACCOUNTS)),
        )


async def ah_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """계좌 선택 버튼: ah:<token>:<계좌 순번|a(전체 보기)|x(취소)>"""
    query = update.callback_query
    await query.answer()
    if not await _check_admin(update):
        return

    _, token, choice = query.data.split(":")
    store = context.user_data.get(PENDING_KEY, {})
    entry = store.get(token)
    if entry is None:
        await _safe_edit(query, "⚠️ 만료된 버튼입니다. 알림을 다시 보내 주세요.")
        return
    if choice == "a":  # 전체 계좌 보기: 대기 건은 그대로 두고 키보드만 바꾼다
        await query.edit_message_reply_markup(_account_keyboard(token, ACCOUNTS, narrowed=False))
        return
    store.pop(token, None)
    if choice == "x":
        await _safe_edit(query, "취소했습니다. 기록하지 않았습니다.")
        return

    account = ACCOUNTS[int(choice)]
    repo = AssetRepository()
    hint_saved = True
    if entry["pattern"]:
        hint_saved = await asyncio.to_thread(repo.add_account_hint, account, entry["pattern"])

    lines = []
    for item in entry["items"]:
        item.pop("unresolved_hint", None)
        item.pop("broker_hint", None)
        item["account_name"] = account
        dup = await asyncio.to_thread(repo.has_same_transaction, item)
        if await asyncio.to_thread(repo.add_transaction, item, entry["memo"]):
            lines.append(describe_transaction(item) + (" ⚠️ 동일 내역이 이미 있었음" if dup else ""))
    logging.info(f"✅ 계좌 선택 {account} (번호 {entry['pattern'] or '없음'}), {len(lines)}건 기록")
    if not entry["pattern"]:
        remembered = f"<b>{html.escape(account)}</b> 로 기록합니다.\n"
    elif hint_saved:
        remembered = (
            f"<code>{html.escape(entry['pattern'])}</code> = <b>{html.escape(account)}</b> 로 기억했습니다.\n"
        )
    else:
        remembered = (
            f"<b>{html.escape(account)}</b> 로 기록합니다. "
            f"⚠️ <code>{html.escape(entry['pattern'])}</code> 번호는 저장하지 못했습니다 (다음에도 다시 묻습니다).\n"
        )
    await _safe_edit(query, f"✅ {remembered}{len(lines)}건 기록 완료\n" + "\n".join(lines))


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
