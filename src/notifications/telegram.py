"""
Telegram сповіщення — відправляє повідомлення при відкритті/закритті угод.
"""
import html
from loguru import logger

from src.config import cfg
from src.database.models import Trade
from src.signals.trend_follower import Signal

try:
    from telegram import Bot
    _TELEGRAM_AVAILABLE = True
except ImportError:
    _TELEGRAM_AVAILABLE = False


class TelegramNotifier:
    def __init__(self):
        self._bot = None
        if _TELEGRAM_AVAILABLE and cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID:
            self._bot = Bot(token=cfg.TELEGRAM_BOT_TOKEN)
            self._chat_id = cfg.TELEGRAM_CHAT_ID

    async def send(self, text: str):
        if self._bot is None:
            logger.info(f"[TELEGRAM ВИМКНЕНО] {text}")
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id, text=text, parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"telegram | помилка надсилання: {e}")

    async def notify_open(
        self,
        trade: Trade,
        signal: Signal,
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
            f"Розмір позиції: <b>${trade.size_usd:.0f}</b> | Ризик: ${trade.risk_usd:.0f}\n"
            f"Сигнал: {signal.source}\n"
            f"ШІ: {ai_ua} — {html.escape(ai_reason[:80])}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"💰 Вільний баланс: <b>${balance_after:.2f}</b>\n"
            f"🔒 В позиціях: <b>${locked:.0f}</b>"
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
            f"💰 Вільний баланс: <b>${balance_after:.2f}</b>\n"
            f"🔒 В позиціях: <b>${locked:.0f}</b>"
        )
        await self.send(text)

    async def notify_daily_summary(self, stats: dict, balance: float, locked: float):
        text = (
            f"📊 <b>Денний звіт</b>\n"
            f"Баланс: ${balance:.2f} (в позиціях: ${locked:.0f})\n"
            f"Угод: {stats.get('total', 0)} | П/З: {stats.get('wins', 0)}/{stats.get('losses', 0)}\n"
            f"Відсоток виграшів: {stats.get('win_rate', 0)*100:.1f}%\n"
            f"Середній R: {stats.get('avg_r', 0):+.2f}R\n"
            f"Профіт-фактор: {stats.get('profit_factor', 0):.2f}\n"
            f"Sharpe: {stats.get('sharpe', 0):.2f}"
        )
        await self.send(text)

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
            f"💰 Вільний баланс: <b>${balance_after:.2f}</b>\n"
            f"🔒 В позиціях: <b>${locked:.0f}</b>"
        )
        await self.send(text)

    async def notify_kill_switch(self, reason: str):
        await self.send(f"🚨 <b>АВАРІЙНА ЗУПИНКА</b>\n{html.escape(reason)}")

    async def notify_signal_skipped(self, coin: str, direction: str, reasons: list[str]):
        dir_ua = "ЛОНГ" if direction == "LONG" else "ШОРТ"
        safe_reasons = ', '.join(html.escape(r) for r in reasons[:3])
        text = (
            f"⏭ <b>Сигнал пропущено</b>\n"
            f"{coin} {dir_ua}\n"
            f"Причини: {safe_reasons}"
        )
        await self.send(text)
