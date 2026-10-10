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
import re
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
)

from bot.handlers.conversation import (
    TEXT_INPUT,
    account_rows,
    begin,
    build_conversation,
    cancel_row,
    expect_button_handler,
    fmt_money,
    make_cancel_handler,
    on_expect_button,  # noqa: F401 (기존 import 경로 유지)
    on_stale_button,  # noqa: F401 (기존 import 경로 유지)
    parse_man_number,
    render,
    render_start,
    save_transaction,
)
from config.constants import ASSET_MAP
from database.connection import fetch_all
from database.repository import AssetRepository

ACCOUNT, CURRENCY, AMOUNT, CONFIRM = range(4)

ACCOUNTS = list(ASSET_MAP.keys())
RECON_MEMO = "잔액 대사 보정"
INCOME_TICKER_NAME = "예수금 이자·기타수익"
CANCEL_ROW = cancel_row("rc")


def _recon(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("recon", {})


def _parse_balance(text: str) -> float | None:
    """실제 잔액 입력: '1,234,567', '123만', '0', '$12.34', '12.34달러' → float (0 이상)."""
    value = parse_man_number(re.sub(r"[,\s원$]|달러|usd", "", str(text or ""), flags=re.IGNORECASE))
    return value if value is not None and value >= 0 else None


def _ledger_balances() -> dict[str, dict[str, float]]:
    """{계좌: {통화: 원장 예수금}} (오늘까지)."""
    today = datetime.now().strftime("%Y-%m-%d")
    balances: dict[str, dict[str, float]] = {}
    for row in AssetRepository().get_account_cash_balances(today):
        balances.setdefault(row["account_name"], {})[row["currency"]] = float(row["net_cash"])
    return balances


def _usd_accounts() -> set[str]:
    """USD 거래 이력이 있는 계좌 (원장 달러 잔액이 0이어도 달러 대사를 고를 수 있도록)."""
    return {
        row[0] for row in fetch_all("SELECT DISTINCT account_name FROM transactions WHERE currency = 'USD'")
    }


# ── 1. 계좌 ─────────────────────────────────────────────


async def recon_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/recon, 메뉴 '잔액 대사': 원장 예수금과 함께 계좌 선택."""
    if not await begin(update):
        return ConversationHandler.END

    balances = await asyncio.to_thread(_ledger_balances)
    usd_accounts = await asyncio.to_thread(_usd_accounts)
    context.user_data["recon"] = {"balances": balances, "usd_accounts": usd_accounts}

    lines = ["🧮 <b>예수금 잔액 대사</b>", "증권사 앱의 실제 예수금과 원장을 맞춥니다.", ""]
    for name in ACCOUNTS:
        cash = balances.get(name, {})
        parts = [fmt_money(cash.get("KRW", 0.0), "KRW")] + (
            [fmt_money(cash["USD"], "USD")] if cash.get("USD") else []
        )
        lines.append(f"{html.escape(name)}: {' / '.join(parts)}")
    lines.append("")
    lines.append("어느 <b>계좌</b>를 맞출까요?")

    markup = InlineKeyboardMarkup(account_rows(ACCOUNTS, "rc") + [CANCEL_ROW])
    await render_start(update, "\n".join(lines), markup, new_data="rc:new")
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
        await render(
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
        f"원장 예수금: <b>{fmt_money(ledger, currency)}</b>\n\n"
        f"증권사 앱의 <b>실제 예수금</b>을 입력하세요. {example}"
    )
    await render(update, text, InlineKeyboardMarkup([CANCEL_ROW]))
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
        f"원장: {fmt_money(recon['ledger'], currency)}\n"
        f"실제: {fmt_money(actual, currency)}\n"
    )
    threshold = 0.005 if currency == "USD" else 0.5
    if abs(diff) < threshold:
        context.user_data.pop("recon", None)
        await update.message.reply_text(
            header + "\n✅ 원장과 일치합니다. 기록할 보정이 없습니다.", parse_mode="HTML"
        )
        return ConversationHandler.END

    sign = "+" if diff > 0 else "−"
    text = header + f"차액: <b>{sign}{fmt_money(abs(diff), currency)}</b>\n\n어떻게 기록할까요? (오늘 일자)"
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
    labels = {"DIVIDEND": "이자·기타 수익", "DEPOSIT": "입금 보정", "WITHDRAW": "출금 보정"}
    await save_transaction(
        query,
        data,
        memo,
        "잔액 대사 보정",
        f"🧮 <b>잔액 대사 완료 ✅</b>\n\n"
        f"{html.escape(recon['account'])} · {currency}\n"
        f"{labels[action]} {fmt_money(abs(recon['diff']), currency)} 기록 → 원장 {fmt_money(recon['actual'], currency)}\n"
        f"<i>오늘 결산부터 반영</i>",
    )
    return ConversationHandler.END


# ── 공통: 취소 / 대화 조립 ─────────────────────────────────

on_cancel = make_cancel_handler("recon", "❌ 잔액 대사를 취소했습니다.")


def build_recon_conversation() -> ConversationHandler:
    expect_button = expect_button_handler()
    return build_conversation(
        "rc",
        entry_points=[
            CommandHandler("recon", recon_start),
            CallbackQueryHandler(recon_start, pattern=r"^rc:new$"),
        ],
        states={
            ACCOUNT: [CallbackQueryHandler(on_account, pattern=r"^rc:acc:\d+$"), expect_button],
            CURRENCY: [CallbackQueryHandler(on_currency, pattern=r"^rc:cur:(KRW|USD)$"), expect_button],
            AMOUNT: [MessageHandler(TEXT_INPUT, on_amount)],
            CONFIRM: [
                CallbackQueryHandler(on_save, pattern=r"^rc:save:(DIVIDEND|DEPOSIT|WITHDRAW)$"),
                expect_button,
            ],
        },
        on_cancel=on_cancel,
    )
