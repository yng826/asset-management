"""bot/handlers/recon_handler.py - 계좌 예수금 잔액 대사 (버튼 단계)

흐름: 계좌 → (USD 잔액이 있는 계좌는) 통화 → 실제 예수금 입력 → 차액 확인 → 기록 방식 선택 → 저장
- 원장 예수금 = DEPOSIT + SELL + DIVIDEND − BUY − WITHDRAW (오늘까지)
- 차액 기록 방식 (오늘 일자, 메모 '잔액 대사 보정')
    · 차액 +: 💰 이자·기타 수익(DIVIDEND, 수익률에 포함) / ➕ 입금 보정(DEPOSIT, 수익률에서 제외)
    · 차액 −: ➖ 출금 보정(WITHDRAW, 수익률에서 제외)
- callback_data 접두어 'rc:'
"""

import asyncio
import html
import logging
import re
import warnings
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.handlers.report_handler import _check_admin, _safe_edit
from config.constants import ASSET_MAP
from database.connection import get_connection
from database.repository import AssetRepository

ACCOUNT, CURRENCY, AMOUNT, CONFIRM = range(4)

ACCOUNTS = list(ASSET_MAP.keys())
RECON_MEMO = "잔액 대사 보정"
INCOME_TICKER_NAME = "예수금 이자·기타수익"
CANCEL_ROW = [InlineKeyboardButton("❌ 취소", callback_data="rc:cancel")]


def _recon(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("recon", {})


def _fmt(value: float, currency: str) -> str:
    return f"${value:,.2f}" if currency == "USD" else f"{value:,.0f}원"


def _parse_balance(text: str) -> float | None:
    """실제 잔액 입력: '1,234,567', '123만', '0', '$12.34', '12.34달러' → float (0 이상)."""
    t = re.sub(r"[,\s원$]|달러|usd", "", str(text or ""), flags=re.IGNORECASE)
    multiplier = 1
    if t.endswith("만"):
        t, multiplier = t[:-1], 10000
    try:
        value = float(t) * multiplier
    except ValueError:
        return None
    return value if value >= 0 else None


def _ledger_balances() -> dict[str, dict[str, float]]:
    """{계좌: {통화: 원장 예수금}} (오늘까지)."""
    today = datetime.now().strftime("%Y-%m-%d")
    balances: dict[str, dict[str, float]] = {}
    for row in AssetRepository().get_account_cash_balances(today):
        balances.setdefault(row["account_name"], {})[row["currency"]] = float(row["net_cash"])
    return balances


def _usd_accounts() -> set[str]:
    """USD 거래 이력이 있는 계좌 (원장 달러 잔액이 0이어도 달러 대사를 고를 수 있도록)."""
    conn = get_connection()
    if not conn:
        return set()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT account_name FROM transactions WHERE currency = 'USD'")
        accounts = {row[0] for row in cur.fetchall()}
        cur.close()
        return accounts
    finally:
        conn.close()


async def _render(update: Update, text: str, markup: InlineKeyboardMarkup | None) -> None:
    if update.callback_query:
        await _safe_edit(update.callback_query, text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=markup)


# ── 1. 계좌 ─────────────────────────────────────────────


async def recon_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/recon, 메뉴 '잔액 대사': 원장 예수금과 함께 계좌 선택."""
    if update.callback_query:
        await update.callback_query.answer()
    if not await _check_admin(update):
        return ConversationHandler.END

    balances = await asyncio.to_thread(_ledger_balances)
    usd_accounts = await asyncio.to_thread(_usd_accounts)
    context.user_data["recon"] = {"balances": balances, "usd_accounts": usd_accounts}

    lines = ["🧮 <b>예수금 잔액 대사</b>", "증권사 앱의 실제 예수금과 원장을 맞춥니다.", ""]
    for name in ACCOUNTS:
        cash = balances.get(name, {})
        parts = [_fmt(cash.get("KRW", 0.0), "KRW")] + ([_fmt(cash["USD"], "USD")] if cash.get("USD") else [])
        lines.append(f"{html.escape(name)}: {' / '.join(parts)}")
    lines.append("")
    lines.append("어느 <b>계좌</b>를 맞출까요?")

    rows = [
        [
            InlineKeyboardButton(name, callback_data=f"rc:acc:{i}")
            for i, name in enumerate(ACCOUNTS[j : j + 2], j)
        ]
        for j in range(0, len(ACCOUNTS), 2)
    ]
    markup = InlineKeyboardMarkup(rows + [CANCEL_ROW])
    if update.callback_query and update.callback_query.data == "rc:new":
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=markup)
    else:
        await _render(update, "\n".join(lines), markup)
    return ACCOUNT


async def on_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    recon = _recon(context)
    recon["account"] = ACCOUNTS[int(query.data.split(":")[2])]

    if recon["account"] in recon["usd_accounts"]:
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🇰🇷 원화 예수금", callback_data="rc:cur:KRW"),
                    InlineKeyboardButton("🇺🇸 달러 예수금", callback_data="rc:cur:USD"),
                ],
                CANCEL_ROW,
            ]
        )
        await _render(
            update, f"🧮 <b>{html.escape(recon['account'])}</b>\n\n어느 <b>통화</b>를 맞출까요?", markup
        )
        return CURRENCY

    recon["currency"] = "KRW"
    return await _ask_amount(update, context)


async def on_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _recon(context)["currency"] = query.data.split(":")[2]
    return await _ask_amount(update, context)


# ── 2. 실제 잔액 ─────────────────────────────────────────


async def _ask_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    recon = _recon(context)
    currency = recon["currency"]
    ledger = recon["balances"].get(recon["account"], {}).get(currency, 0.0)
    recon["ledger"] = ledger
    example = "예) 12.34" if currency == "USD" else "예) 1,234,567 또는 123만"
    text = (
        f"🧮 <b>{html.escape(recon['account'])}</b> · {currency}\n"
        f"원장 예수금: <b>{_fmt(ledger, currency)}</b>\n\n"
        f"증권사 앱의 <b>실제 예수금</b>을 입력하세요. {example}"
    )
    await _render(update, text, InlineKeyboardMarkup([CANCEL_ROW]))
    return AMOUNT


async def on_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    recon = _recon(context)
    actual = _parse_balance(update.message.text)
    if actual is None:
        await update.message.reply_text("숫자로 입력해 주세요. 예) 1,234,567 / 123만 / 0")
        return AMOUNT

    currency = recon["currency"]
    diff = round(actual - recon["ledger"], 2)
    recon["actual"], recon["diff"] = actual, diff
    header = (
        f"🧮 <b>잔액 대사 확인</b>\n\n"
        f"계좌: <b>{html.escape(recon['account'])}</b> · {currency}\n"
        f"원장: {_fmt(recon['ledger'], currency)}\n"
        f"실제: {_fmt(actual, currency)}\n"
    )
    threshold = 0.005 if currency == "USD" else 0.5
    if abs(diff) < threshold:
        context.user_data.pop("recon", None)
        await update.message.reply_text(
            header + "\n✅ 원장과 일치합니다. 기록할 보정이 없습니다.", parse_mode="HTML"
        )
        return ConversationHandler.END

    sign = "+" if diff > 0 else "−"
    text = header + f"차액: <b>{sign}{_fmt(abs(diff), currency)}</b>\n\n어떻게 기록할까요? (오늘 일자)"
    if diff > 0:
        buttons = [
            [InlineKeyboardButton("💰 이자·기타 수익으로", callback_data="rc:save:DIVIDEND")],
            [InlineKeyboardButton("➕ 입금 보정으로", callback_data="rc:save:DEPOSIT")],
        ]
        text += "\n<i>수익: 수익률에 포함 / 입금 보정: 외부 입금으로 보고 수익률에서 제외</i>"
    else:
        buttons = [[InlineKeyboardButton("➖ 출금 보정으로", callback_data="rc:save:WITHDRAW")]]
        text += "\n<i>출금 보정: 외부 출금으로 보고 수익률에서 제외</i>"
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons + [CANCEL_ROW])
    )
    return CONFIRM


# ── 3. 저장 ──────────────────────────────────────────────


async def on_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    recon = context.user_data.pop("recon", {})
    action = query.data.split(":")[2]
    currency = recon["currency"]
    cash_code = "CASH_USD" if currency == "USD" else "CASH_KRW"

    data = {
        "trans_date": datetime.now().strftime("%Y-%m-%d"),
        "account_name": recon["account"],
        "ticker_name": INCOME_TICKER_NAME if action == "DIVIDEND" else f"{currency}_CASH",
        "ticker_code": cash_code,
        "action_type": action,
        "total_amount": abs(recon["diff"]),
        "currency": currency,
    }
    memo = RECON_MEMO + (" (이자·기타 수익)" if action == "DIVIDEND" else "")
    success = await asyncio.to_thread(AssetRepository().add_transaction, data, memo)
    if success:
        logging.info(f"✅ 잔액 대사 보정 저장: {data}")
        labels = {"DIVIDEND": "이자·기타 수익", "DEPOSIT": "입금 보정", "WITHDRAW": "출금 보정"}
        await _safe_edit(
            query,
            f"🧮 <b>잔액 대사 완료 ✅</b>\n\n"
            f"{html.escape(recon['account'])} · {currency}\n"
            f"{labels[action]} {_fmt(abs(recon['diff']), currency)} 기록 → 원장 {_fmt(recon['actual'], currency)}\n"
            f"<i>오늘 16:00 결산부터 반영</i>",
        )
    else:
        logging.error(f"❌ 잔액 대사 보정 저장 실패: {data}")
        await _safe_edit(query, "❌ DB 저장 중 오류가 발생했습니다. 다시 시도해 주세요.")
    return ConversationHandler.END


# ── 공통 ─────────────────────────────────────────────────


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("recon", None)
    if update.callback_query:
        await update.callback_query.answer()
        await _safe_edit(update.callback_query, "❌ 잔액 대사를 취소했습니다.")
    return ConversationHandler.END


async def on_expect_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("👆 위 버튼에서 선택하거나 ❌ 취소를 눌러 주세요.")


async def on_stale_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer("지난 단계의 버튼입니다. 최근 메시지의 버튼을 눌러 주세요.")


def build_recon_conversation() -> ConversationHandler:
    text = filters.TEXT & ~filters.COMMAND
    expect_button = MessageHandler(text, on_expect_button)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*per_message.*")
        return ConversationHandler(
            entry_points=[
                CommandHandler("recon", recon_start),
                CallbackQueryHandler(recon_start, pattern=r"^rc:new$"),
            ],
            states={
                ACCOUNT: [CallbackQueryHandler(on_account, pattern=r"^rc:acc:\d+$"), expect_button],
                CURRENCY: [CallbackQueryHandler(on_currency, pattern=r"^rc:cur:(KRW|USD)$"), expect_button],
                AMOUNT: [MessageHandler(text, on_amount)],
                CONFIRM: [
                    CallbackQueryHandler(on_save, pattern=r"^rc:save:(DIVIDEND|DEPOSIT|WITHDRAW)$"),
                    expect_button,
                ],
            },
            fallbacks=[
                CallbackQueryHandler(on_cancel, pattern=r"^rc:cancel$"),
                CallbackQueryHandler(on_stale_button, pattern=r"^rc:"),
            ],
            allow_reentry=True,
            conversation_timeout=15 * 60,
        )
