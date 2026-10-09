"""bot/handlers/trade_input_handler.py - 버튼 단계 거래 입력 (핑퐁)

흐름: 계좌 → 유형 → 종목(보유 종목 버튼 / 이름 검색) → 수량 → 단가(또는 금액) → 일자 → 확인 → 저장
- 매수/매도: 종목 → 수량 → 단가
- 배당: 종목 → 금액(세후 실수령 총액)
- 입금/출금: 금액 ('$100', '100달러' 처럼 입력하면 USD)
- callback_data 접두어 'tr:'
"""

import asyncio
import html
import logging
import re
import warnings
from datetime import datetime, timedelta

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
from core.ticker_master import (
    MARKET_LABELS,
    classify_market,
    currency_for_market,
    is_us_symbol_candidate,
    resolve_display_name,
    search_tickers,
)
from database.repository import AssetRepository

ACCOUNT, ACTION, TICKER, QUANTITY, PRICE, DATE, CONFIRM = range(7)

ACCOUNTS = list(ASSET_MAP.keys())
ACTIONS = [
    ("BUY", "⬆️ 매수"),
    ("SELL", "⬇️ 매도"),
    ("DIVIDEND", "💰 배당"),
    ("DEPOSIT", "➕ 입금"),
    ("WITHDRAW", "➖ 출금"),
]
ACTION_LABELS = dict(ACTIONS)

# 확인 단계에서 최근 종가 대비 단가 차이가 이 비율(%) 이상이면 경고
PRICE_GAP_WARN_PCT = 20

CANCEL_ROW = [InlineKeyboardButton("❌ 취소", callback_data="tr:cancel")]


def _trade(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.user_data.setdefault("trade", {})


def _fmt_amount(value: float, currency: str) -> str:
    return f"${value:,.2f}" if currency == "USD" else f"{value:,.0f}원"


def _fmt_qty(value: float) -> str:
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def _parse_number(text: str) -> float | None:
    """'72,000', '72000원', '1.5', '2만', '2.5만' → float (양수만)."""
    t = re.sub(r"[,\s원주개]", "", str(text or ""))
    multiplier = 1
    if t.endswith("만"):
        t, multiplier = t[:-1], 10000
    try:
        value = float(t) * multiplier
    except ValueError:
        return None
    return value if value > 0 else None


def _parse_cash_amount(text: str) -> tuple[float | None, str]:
    """입출금 금액: '$100', '100달러', '100 usd' → USD, 그 외 KRW."""
    raw = str(text or "").strip()
    is_usd = bool(re.search(r"\$|달러|usd", raw, re.IGNORECASE))
    cleaned = re.sub(r"\$|달러|usd", "", raw, flags=re.IGNORECASE)
    return _parse_number(cleaned), ("USD" if is_usd else "KRW")


def _parse_date(text: str) -> str | None:
    """'2026-10-08', '10-08', '10/8', '1008' → 'YYYY-MM-DD' (미래 일자는 거부)."""
    t = str(text or "").strip()
    today = datetime.now()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d"):
        try:
            d = datetime.strptime(t, fmt)
            break
        except ValueError:
            d = None
    if d is None:
        # 'MM-DD', 'M/D', 'MMDD' (구분자 없는 경우 4자리만 허용)
        m = re.match(r"^(\d{1,2})[-./](\d{1,2})$", t) or re.match(r"^(\d{2})(\d{2})$", t)
        if not m:
            return None
        try:
            d = datetime(today.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    if d.date() > today.date():
        return None
    return d.strftime("%Y-%m-%d")


def _summary(trade: dict) -> str:
    """현재까지 선택한 값 요약 (각 단계 메시지 상단)."""
    parts = []
    if trade.get("account"):
        parts.append(trade["account"])
    if trade.get("action"):
        parts.append(ACTION_LABELS[trade["action"]])
    if trade.get("ticker"):
        t = trade["ticker"]
        parts.append(f"{t['name']} ({t['code']})")
    if trade.get("quantity"):
        parts.append(f"{_fmt_qty(trade['quantity'])}주")
    line = " · ".join(html.escape(p) for p in parts)
    return f"🧾 <b>거래 입력</b>\n{line}\n\n" if line else "🧾 <b>거래 입력</b>\n\n"


async def _render(update: Update, text: str, markup: InlineKeyboardMarkup | None) -> None:
    """버튼에서 온 경우 같은 메시지를 수정, 텍스트 입력에서 온 경우 새 메시지로 응답."""
    if update.callback_query:
        await _safe_edit(update.callback_query, text, reply_markup=markup)
    else:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=markup)


# ── 1. 계좌 ─────────────────────────────────────────────


async def trade_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/trade 또는 메뉴 '거래 입력' 버튼: 계좌 선택부터 시작."""
    if update.callback_query:
        await update.callback_query.answer()
    if not await _check_admin(update):
        return ConversationHandler.END

    context.user_data["trade"] = {}
    # 메뉴에서 시작(tr:new)하면 메뉴 메시지는 남기고 새 메시지로 진행
    from_menu = bool(update.callback_query and update.callback_query.data == "tr:new")
    rows = [
        [
            InlineKeyboardButton(name, callback_data=f"tr:acc:{i}")
            for i, name in enumerate(ACCOUNTS[j : j + 2], j)
        ]
        for j in range(0, len(ACCOUNTS), 2)
    ]
    text = _summary({}) + "어느 <b>계좌</b>인가요?"
    markup = InlineKeyboardMarkup(rows + [CANCEL_ROW])
    if from_menu:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await _render(update, text, markup)
    return ACCOUNT


async def on_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":")[2])
    trade = _trade(context)
    trade["account"] = ACCOUNTS[idx]

    buttons = [InlineKeyboardButton(label, callback_data=f"tr:act:{key}") for key, label in ACTIONS]
    markup = InlineKeyboardMarkup([buttons[:3], buttons[3:], CANCEL_ROW])
    await _render(update, _summary(trade) + "어떤 <b>거래</b>인가요?", markup)
    return ACTION


# ── 2. 유형 ─────────────────────────────────────────────


async def on_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    trade = _trade(context)
    trade["action"] = query.data.split(":")[2]

    if trade["action"] in ("DEPOSIT", "WITHDRAW"):
        await _render(
            update,
            _summary(trade) + "<b>금액</b>을 입력하세요.\n예) 500000, 50만, $1000 (달러는 $ 또는 '달러')",
            InlineKeyboardMarkup([CANCEL_ROW]),
        )
        return PRICE

    return await _show_ticker_prompt(update, context)


# ── 3. 종목 ─────────────────────────────────────────────


async def _show_ticker_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """해당 계좌 보유 종목을 버튼으로 제시 + 이름 검색 안내."""
    trade = _trade(context)
    holdings = await asyncio.to_thread(AssetRepository().get_current_holdings)
    candidates = [
        {
            "code": h["ticker_code"],
            "name": h["ticker_name"],
            "market": classify_market(h["ticker_code"]),
            "traded": True,
        }
        for h in holdings
        if h["account_name"] == trade["account"] and h["ticker_code"]
    ]
    trade["candidates"] = candidates

    rows = _candidate_rows(candidates)
    if candidates:
        text = "보유 종목에서 고르거나, <b>종목명을 입력</b>해 검색하세요."
    else:
        text = "<b>종목명</b>을 입력해 검색하세요. (예: 삼성전자, kodex 나스닥, NVDA)"
    await _render(update, _summary(trade) + text, InlineKeyboardMarkup(rows + [CANCEL_ROW]))
    return TICKER


def _candidate_rows(candidates: list[dict]) -> list[list[InlineKeyboardButton]]:
    rows = []
    for i, c in enumerate(candidates):
        name = c["name"] if len(c["name"]) <= 28 else c["name"][:27] + "…"
        label = f"{name} · {c['code']}"
        if c["market"] in ("US", "CRYPTO", "FUND"):
            label += f" ({MARKET_LABELS[c['market']]})"
        rows.append([InlineKeyboardButton(label, callback_data=f"tr:tk:{i}")])
    return rows


async def on_ticker_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """종목명 텍스트 입력 → 검색 결과 버튼."""
    trade = _trade(context)
    keyword = update.message.text.strip()
    results = await asyncio.to_thread(search_tickers, keyword, 8)
    trade["candidates"] = results
    trade["us_symbol"] = keyword.upper() if is_us_symbol_candidate(keyword) else None

    rows = _candidate_rows(results)
    if trade["us_symbol"] and all(r["code"] != trade["us_symbol"] for r in results):
        rows.append(
            [InlineKeyboardButton(f"🇺🇸 {trade['us_symbol']} 티커로 직접 지정", callback_data="tr:tk:us")]
        )

    if rows:
        text = f"'{html.escape(keyword)}' 검색 결과입니다. 선택하거나 다른 이름으로 다시 입력하세요."
    else:
        text = f"'{html.escape(keyword)}' 검색 결과가 없습니다. 다른 이름이나 종목코드로 입력하세요."
    await _render(update, _summary(trade) + text, InlineKeyboardMarkup(rows + [CANCEL_ROW]))
    return TICKER


async def on_ticker_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    trade = _trade(context)
    key = query.data.split(":")[2]

    if key == "us":
        # 마스터에 없는 미국 티커(ETF 등): 실제 시세가 조회되는 경우에만 허용
        await query.answer("⏳ 시세 확인 중...")
        from core.fetcher.us_stock import fetch_us_stock_close

        symbol = trade.get("us_symbol")
        price = await asyncio.to_thread(fetch_us_stock_close, symbol) if symbol else None
        if not price:
            await update.effective_message.reply_text(
                f"⚠️ {html.escape(symbol or '')} 시세를 찾을 수 없습니다."
            )
            return TICKER
        item = {"code": symbol, "name": symbol, "market": "US", "traded": False}
    else:
        await query.answer()
        candidates = trade.get("candidates") or []
        idx = int(key)
        if idx >= len(candidates):
            return TICKER
        item = candidates[idx]

    trade["ticker"] = {"code": item["code"], "name": resolve_display_name(item), "market": item["market"]}
    trade["currency"] = currency_for_market(item["market"])

    if trade["action"] == "DIVIDEND":
        unit = "달러" if trade["currency"] == "USD" else "원"
        text = f"입금된 <b>배당금</b>(세후 실수령 총액, {unit})을 입력하세요."
        await _render(update, _summary(trade) + text, InlineKeyboardMarkup([CANCEL_ROW]))
        return PRICE

    await _render(
        update, _summary(trade) + "<b>수량</b>을 입력하세요. (소수 가능)", InlineKeyboardMarkup([CANCEL_ROW])
    )
    return QUANTITY


# ── 4. 수량 / 5. 단가·금액 ────────────────────────────────


async def on_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    trade = _trade(context)
    qty = _parse_number(update.message.text)
    if qty is None:
        await update.message.reply_text("⚠️ 수량은 0보다 큰 숫자로 입력하세요. 예) 3, 0.5")
        return QUANTITY

    trade["quantity"] = qty
    unit = "달러" if trade["currency"] == "USD" else "원"
    await _render(
        update,
        _summary(trade) + f"체결 <b>단가</b>({unit})를 입력하세요.",
        InlineKeyboardMarkup([CANCEL_ROW]),
    )
    return PRICE


async def on_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    trade = _trade(context)
    action = trade["action"]

    if action in ("DEPOSIT", "WITHDRAW"):
        amount, currency = _parse_cash_amount(update.message.text)
        trade["currency"] = currency
    else:
        amount = _parse_number(update.message.text)
    if amount is None:
        await update.message.reply_text("⚠️ 0보다 큰 숫자로 입력하세요. 예) 72000, 7.2만, 185.5")
        return PRICE

    if action in ("BUY", "SELL"):
        trade["unit_price"] = amount
        total = trade["quantity"] * amount
        trade["total_amount"] = round(total, 2) if trade["currency"] == "USD" else round(total)
    else:
        trade["unit_price"] = None
        trade["total_amount"] = amount

    today = datetime.now()
    buttons = [
        InlineKeyboardButton(
            label, callback_data=f"tr:date:{(today - timedelta(days=d)).strftime('%Y-%m-%d')}"
        )
        for label, d in (("오늘", 0), ("어제", 1), ("그제", 2))
    ]
    await _render(
        update,
        _summary(trade) + "거래 <b>일자</b>를 고르거나 입력하세요. (예: 10-08, 2026-10-08)",
        InlineKeyboardMarkup([buttons, CANCEL_ROW]),
    )
    return DATE


# ── 6. 일자 → 7. 확인 ────────────────────────────────────


async def on_date_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    _trade(context)["trans_date"] = update.callback_query.data.split(":", 2)[2]
    return await _show_confirm(update, context)


async def on_date_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    trans_date = _parse_date(update.message.text)
    if trans_date is None:
        await update.message.reply_text(
            "⚠️ 날짜 형식이 올바르지 않거나 미래 일자입니다. 예) 10-08, 2026-10-08"
        )
        return DATE
    _trade(context)["trans_date"] = trans_date
    return await _show_confirm(update, context)


async def _show_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    trade = _trade(context)
    currency = trade["currency"]
    lines = [
        f"계좌: <b>{html.escape(trade['account'])}</b>",
        f"유형: <b>{ACTION_LABELS[trade['action']]}</b>",
    ]
    if trade.get("ticker"):
        t = trade["ticker"]
        market = MARKET_LABELS.get(t["market"], t["market"])
        lines.append(f"종목: <b>{html.escape(t['name'])}</b> ({html.escape(t['code'])}, {market})")
    if trade["action"] in ("BUY", "SELL"):
        lines.append(f"수량: <b>{_fmt_qty(trade['quantity'])}</b>")
        lines.append(f"단가: <b>{_fmt_amount(trade['unit_price'], currency)}</b>")
    lines.append(f"총액: <b>{_fmt_amount(trade['total_amount'], currency)}</b>")
    lines.append(f"일자: <b>{trade['trans_date']}</b>")

    # 오타 방지: 최근 종가와 단가 차이가 크면 경고 (DB에 시세가 있는 종목만)
    if trade["action"] in ("BUY", "SELL"):
        from core.calculator import get_latest_prices_map

        latest = (await asyncio.to_thread(get_latest_prices_map)).get(trade["ticker"]["code"])
        if latest and latest["close_price"] > 0:
            gap = (trade["unit_price"] / latest["close_price"] - 1) * 100
            if abs(gap) >= PRICE_GAP_WARN_PCT:
                lines.append(
                    f"\n⚠️ 최근 종가 {_fmt_amount(latest['close_price'], currency)} ({latest['price_date']}) "
                    f"대비 단가가 {gap:+.1f}% 다릅니다. 확인해 주세요."
                )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ 저장", callback_data="tr:save"),
                InlineKeyboardButton("🔄 처음부터", callback_data="tr:start"),
            ],
            CANCEL_ROW,
        ]
    )
    await _render(update, "🧾 <b>거래 입력 확인</b>\n\n" + "\n".join(lines), markup)
    return CONFIRM


async def on_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    trade = context.user_data.pop("trade", {})
    ticker = trade.get("ticker") or {}

    data = {
        "trans_date": trade["trans_date"],
        "account_name": trade["account"],
        "ticker_name": ticker.get("name"),
        "ticker_code": ticker.get("code"),
        "action_type": trade["action"],
        "quantity": trade.get("quantity"),
        "unit_price": trade.get("unit_price"),
        "total_amount": trade["total_amount"],
        "currency": trade["currency"],
    }
    success = await asyncio.to_thread(AssetRepository().add_transaction, data, "[버튼입력]")
    if success:
        logging.info(f"✅ 버튼 거래 입력 저장: {data}")
        await _safe_edit(query, query.message.text_html.replace("거래 입력 확인", "거래 기록 완료 ✅", 1))
    else:
        logging.error(f"❌ 버튼 거래 입력 저장 실패: {data}")
        await _safe_edit(query, "❌ DB 저장 중 오류가 발생했습니다. 다시 시도해 주세요.")
    return ConversationHandler.END


# ── 공통: 취소 / 잘못된 입력 / 만료 버튼 ──────────────────────


async def on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("trade", None)
    if update.callback_query:
        await update.callback_query.answer()
        await _safe_edit(update.callback_query, "❌ 거래 입력을 취소했습니다.")
    return ConversationHandler.END


async def on_expect_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """버튼을 기다리는 단계에서 텍스트가 오면 안내 (Gemini 자유 입력으로 넘어가지 않도록 흡수)."""
    await update.message.reply_text("👆 위 버튼에서 선택하거나 ❌ 취소를 눌러 주세요.")


async def on_stale_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """현재 단계와 맞지 않는(지난) 거래 입력 버튼."""
    await update.callback_query.answer("지난 단계의 버튼입니다. 최근 메시지의 버튼을 눌러 주세요.")


def build_trade_conversation() -> ConversationHandler:
    text = filters.TEXT & ~filters.COMMAND
    expect_button = MessageHandler(text, on_expect_button)
    # 대화 상태를 메시지가 아닌 사용자·채팅 단위로 추적하는 것이 의도이므로 per_message 경고는 무시
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*per_message.*")
        return _build_conversation(text, expect_button)


def _build_conversation(text, expect_button) -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("trade", trade_start),
            CallbackQueryHandler(trade_start, pattern=r"^tr:(start|new)$"),
        ],
        states={
            ACCOUNT: [CallbackQueryHandler(on_account, pattern=r"^tr:acc:\d+$"), expect_button],
            ACTION: [CallbackQueryHandler(on_action, pattern=r"^tr:act:[A-Z]+$"), expect_button],
            TICKER: [
                CallbackQueryHandler(on_ticker_pick, pattern=r"^tr:tk:(\d+|us)$"),
                MessageHandler(text, on_ticker_search),
            ],
            QUANTITY: [MessageHandler(text, on_quantity)],
            PRICE: [MessageHandler(text, on_price)],
            DATE: [
                CallbackQueryHandler(on_date_button, pattern=r"^tr:date:\d{4}-\d{2}-\d{2}$"),
                MessageHandler(text, on_date_text),
            ],
            CONFIRM: [CallbackQueryHandler(on_save, pattern=r"^tr:save$"), expect_button],
        },
        fallbacks=[
            CallbackQueryHandler(on_cancel, pattern=r"^tr:cancel$"),
            CallbackQueryHandler(trade_start, pattern=r"^tr:(start|new)$"),
            CallbackQueryHandler(on_stale_button, pattern=r"^tr:"),
        ],
        allow_reentry=True,
        conversation_timeout=15 * 60,
    )
