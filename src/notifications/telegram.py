"""
Telegram сповіщення + інтерактивний бот.

Команди:
  /start    — головне меню з кнопками
  /history  — останні 15 закритих угод
  /pnl      — загальний PnL та статистика
  /positions — відкриті позиції
"""
import html
from loguru import logger
from sqlalchemy import select

from src.config import cfg
from src.database.db import AsyncSessionLocal
from src.database.models import Trade, TradeStatus

try:
    from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        ContextTypes,
    )
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False


# ── Helpers для побудови текстів ────────────────────────────────────

async def _history_text() -> str:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Trade)
            .where(Trade.status != TradeStatus.OPEN)
            .order_by(Trade.closed_at.desc())
            .limit(15)
        )
        trades = res.scalars().all()

    if not trades:
        return "📋 <b>Угод ще немає</b>"

    status_short = {
        "CLOSED_SL": "SL", "CLOSED_TP2": "TP2",
        "CLOSED_TRAILING": "Trail", "CLOSED_TIME": "Time",
        "CLOSED_EMERGENCY": "Emrg", "CLOSED_HERMES_EXIT": "Hermes",
    }
    lines = ["📋 <b>Останні закриті угоди:</b>\n"]
    for t in trades:
        pnl = t.pnl_usd or 0
        r = t.r_multiple or 0
        emoji = "✅" if pnl >= 0 else "❌"
        direction = "LONG" if t.direction == "LONG" else "SHORT"
        date = t.closed_at.strftime("%d.%m %H:%M") if t.closed_at else "—"
        st = status_short.get(t.status, t.status[:5])
        lines.append(
            f"{emoji} <b>#{t.id}</b> {t.coin} {direction} "
            f"| <code>{pnl:+.2f}$</code> <code>{r:+.2f}R</code> "
            f"| {st} | {date}"
        )
    return "\n".join(lines)


async def _pnl_text() -> str:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Trade).where(Trade.status != TradeStatus.OPEN)
        )
        closed = res.scalars().all()

        res2 = await session.execute(
            select(Trade).where(Trade.status == TradeStatus.OPEN)
        )
        open_trades = res2.scalars().all()

    total = len(closed)
    wins = sum(1 for t in closed if (t.r_multiple or 0) > 0)
    losses = total - wins
    total_pnl = sum(t.pnl_usd or 0 for t in closed)
    avg_r = sum(t.r_multiple or 0 for t in closed) / total if total else 0
    win_rate = wins / total * 100 if total else 0
    locked = sum(t.size_usd / (t.leverage or 5) for t in open_trades)
    free = cfg.INITIAL_BALANCE + total_pnl - locked
    pnl_emoji = "📈" if total_pnl >= 0 else "📉"

    return (
        f"{pnl_emoji} <b>Загальний PnL</b>\n\n"
        f"💰 Реалізований: <code>{total_pnl:+.2f} USD</code>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Угод: <b>{total}</b> | ✅ {wins} / ❌ {losses}\n"
        f"Win rate: <b>{win_rate:.1f}%</b>\n"
        f"Середній R: <code>{avg_r:+.2f}R</code>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Початковий баланс: <b>${cfg.INITIAL_BALANCE:.0f}</b>\n"
        f"Вільний зараз: <b>${free:.2f}</b>\n"
        f"В позиціях: <b>${locked:.0f}</b> ({len(open_trades)} поз.)"
    )


async def _positions_text() -> str:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Trade)
            .where(Trade.status == TradeStatus.OPEN)
            .order_by(Trade.created_at.desc())
        )
        open_trades = res.scalars().all()

    if not open_trades:
        return "📍 <b>Немає відкритих позицій</b>"

    lines = [f"📍 <b>Відкриті позиції ({len(open_trades)}):</b>\n"]
    for t in open_trades:
        dir_icon = "🟢" if t.direction == "LONG" else "🔴"
        margin = t.size_usd / (t.leverage or 5)
        opened = t.created_at.strftime("%d.%m %H:%M") if t.created_at else "—"
        tp1_mark = " ✔BE" if t.tp1_hit else ""
        lines.append(
            f"{dir_icon} <b>#{t.id} {t.coin}</b> {t.direction}\n"
            f"  Вхід: <code>{t.entry_price:.4f}</code> | Стоп: <code>{t.stop_price:.4f}</code>\n"
            f"  TP1: <code>{t.tp1_price:.4f}</code>{tp1_mark} | TP2: <code>{t.tp2_price:.4f}</code>\n"
            f"  Маржа: <b>${margin:.0f}</b> x{t.leverage or 5} | {opened}"
        )
    return "\n\n".join(lines)


def _main_keyboard() -> "InlineKeyboardMarkup":
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 Історія", callback_data="history"),
            InlineKeyboardButton("💰 PnL", callback_data="pnl"),
        ],
        [
            InlineKeyboardButton("📍 Позиції", callback_data="positions"),
        ],
    ])


# ── Handler functions ────────────────────────────────────────────────

async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>Торговий Бот</b>\nОберіть дію:",
        parse_mode="HTML",
        reply_markup=_main_keyboard(),
    )


async def _cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await _history_text()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=_main_keyboard())


async def _cmd_pnl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await _pnl_text()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=_main_keyboard())


async def _cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await _positions_text()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=_main_keyboard())


async def _on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "history":
        text = await _history_text()
    elif data == "pnl":
        text = await _pnl_text()
    elif data == "positions":
        text = await _positions_text()
    else:
        return

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=_main_keyboard())


# ── TelegramNotifier ────────────────────────────────────────────────

class TelegramNotifier:
    def __init__(self):
        self._app: "Application | None" = None

        if not (_TELEGRAM_AVAILABLE and cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID):
            return

        self._chat_id = cfg.TELEGRAM_CHAT_ID
        self._app = (
            Application.builder()
            .token(cfg.TELEGRAM_BOT_TOKEN)
            .build()
        )
        self._app.add_handler(CommandHandler("start", _cmd_start))
        self._app.add_handler(CommandHandler("history", _cmd_history))
        self._app.add_handler(CommandHandler("pnl", _cmd_pnl))
        self._app.add_handler(CommandHandler("positions", _cmd_positions))
        self._app.add_handler(CallbackQueryHandler(_on_button))

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start_polling(self):
        """Starts receiving commands from Telegram in background."""
        if self._app is None:
            return
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("telegram | polling started")

    async def stop_polling(self):
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    # ── Sending ──────────────────────────────────────────────────────

    async def send(self, text: str, reply_markup=None):
        if self._app is None:
            logger.info(f"[TELEGRAM ВИМКНЕНО] {text}")
            return
        try:
            await self._app.bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception as e:
            logger.warning(f"telegram | помилка: {e}")

    # ── Notifications ────────────────────────────────────────────────

    async def notify_open(
        self,
        trade: Trade,
        signal,
        ai_decision: str,
        ai_reason: str,
        balance_after: float,
        locked: float,
    ):
        emoji = "🟢" if signal.direction == "LONG" else "🔴"
        dir_ua = "ЛОНГ" if signal.direction == "LONG" else "ШОРТ"
        ai_ua = {"take": "взяти", "reduced": "зменшений", "skip": "пропустити"}.get(ai_decision, ai_decision)
        text = (
            f"{emoji} <b>УГОДА ВІДКРИТА #{trade.id}</b>\n"
            f"Монета: <b>{signal.coin}</b> | {dir_ua}\n"
            f"Вхід: <code>{signal.entry:.4f}</code>\n"
            f"Стоп: <code>{signal.suggested_stop:.4f}</code>\n"
            f"ТП1: <code>{signal.suggested_tp1:.4f}</code>\n"
            f"ТП2: <code>{signal.suggested_tp2:.4f}</code>\n"
            f"Розмір: <b>${trade.size_usd:.0f}</b> | Ризик: ${trade.risk_usd:.0f}\n"
            f"ШІ: {ai_ua} — {html.escape(ai_reason[:80])}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Вільний: <b>${balance_after:.2f}</b> | 🔒 В позиціях: <b>${locked:.0f}</b>"
        )
        await self.send(text)

    async def notify_close(self, trade: Trade, balance_after: float, locked: float):
        pnl = trade.pnl_usd or 0
        r = trade.r_multiple or 0
        emoji = "✅" if pnl >= 0 else "❌"
        dir_ua = "ЛОНГ" if trade.direction == "LONG" else "ШОРТ"
        status_ua = {
            "CLOSED_SL": "Стоп-лосс",
            "CLOSED_TP2": "Тейк-профіт 2",
            "CLOSED_TRAILING": "Трейлінг стоп",
            "CLOSED_TIME": "Час вийшов (24h)",
            "CLOSED_EMERGENCY": "Аварійний вихід",
            "CLOSED_HERMES_EXIT": "Hermes достроково закрив",
        }.get(trade.status, trade.status)
        text = (
            f"{emoji} <b>УГОДА ЗАКРИТА #{trade.id}</b>\n"
            f"Монета: <b>{trade.coin}</b> | {dir_ua}\n"
            f"Причина: {status_ua}\n"
            f"Вихід: <code>{trade.exit_price:.4f}</code>\n"
            f"PnL: <code>{pnl:+.2f} USD</code> | <code>{r:+.2f}R</code>\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Вільний: <b>${balance_after:.2f}</b> | 🔒 В позиціях: <b>${locked:.0f}</b>"
        )
        await self.send(text)

    async def notify_daily_summary(self, stats: dict, balance: float, locked: float):
        text = (
            f"📊 <b>Денний звіт</b>\n"
            f"Баланс: ${balance:.2f} (в позиціях: ${locked:.0f})\n"
            f"Угод: {stats.get('total', 0)} | П/З: {stats.get('wins', 0)}/{stats.get('losses', 0)}\n"
            f"Win rate: {stats.get('win_rate', 0)*100:.1f}%\n"
            f"Середній R: {stats.get('avg_r', 0):+.2f}R\n"
            f"Profit factor: {stats.get('profit_factor', 0):.2f}"
        )
        await self.send(text, reply_markup=_main_keyboard() if _TELEGRAM_AVAILABLE else None)

    async def notify_hermes_exit(
        self,
        trade: Trade,
        exit_price: float,
        trigger: str,
        reason: str,
        balance_after: float,
        locked: float,
    ):
        dir_ua = "ЛОНГ" if trade.direction == "LONG" else "ШОРТ"
        text = (
            f"🚪 <b>HERMES ДОСТРОКОВО ЗАКРИВ #{trade.id}</b>\n"
            f"Монета: <b>{trade.coin}</b> | {dir_ua}\n"
            f"Вихід: <code>{exit_price:.4f}</code>\n"
            f"Тригер: {html.escape(trigger[:100])}\n"
            f"Рішення: {html.escape(reason[:120])}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Вільний: <b>${balance_after:.2f}</b> | 🔒 В позиціях: <b>${locked:.0f}</b>"
        )
        await self.send(text)

    async def notify_kill_switch(self, reason: str):
        await self.send(f"🚨 <b>АВАРІЙНА ЗУПИНКА</b>\n{html.escape(reason)}")

    async def notify_signal_skipped(self, coin: str, direction: str, reasons: list[str]):
        dir_ua = "ЛОНГ" if direction == "LONG" else "ШОРТ"
        safe_reasons = ', '.join(html.escape(r) for r in reasons[:3])
        await self.send(
            f"⏭ <b>Сигнал пропущено</b>\n{coin} {dir_ua}\nПричини: {safe_reasons}"
        )
