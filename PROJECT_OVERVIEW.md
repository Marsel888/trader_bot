# Trade Bot — Опис проєкту

## Що це таке

Автономний крипто-торговий бот на Python 3.14, що працює в режимі **paper trading** (симуляція без реальних грошей). Бот відстежує 4 монети (BTC, ETH, SOL, BNB), знаходить торгові сигнали за технічним аналізом, фільтрує їх через локальний AI (Hermes 3 via Ollama), і відправляє сповіщення в Telegram.

---

## Архітектура — схема потоку даних

```
Binance REST API
       |
       v
price_collector.py   (отримує свічки 1h/4h/1d для 4 монет)
       |
       v
indicators.py        (розраховує EMA, ATR, ADX)
       |
       v
signal_engine.py     (запускає генератори сигналів)
      / \
     /   \
trend_follower.py   breakout.py
(EMA cross + ADX)   (пробій 20-бар хай/лоу + об'єм)
     \   /
      \ /
       v
pre_checks.py        (правила: мультиТФ тренд, R:R, kill switch...)
       |
       v
hermes_filter.py     (AI: Hermes 3 локально / Claude / safe default)
       |
       v
paper_executor.py    (відкриває віртуальну позицію)
       |
       v
portfolio.py         (моніторить SL/TP/trailing stop/time exit)
       |
       v
journal.py           (записує угоди в SQLite DB)
       |
       v
telegram.py          (шле сповіщення в Telegram)
```

---

## Файли та що вони роблять

### Ядро

| Файл | Призначення |
|------|-------------|
| `orchestrator.py` | Головний цикл. Запускає всі компоненти кожен тік (60 сек) |
| `src/config.py` | Читає `.env`, зберігає всі параметри в одному місці |

### Дані

| Файл | Призначення |
|------|-------------|
| `src/data/price_collector.py` | Завантажує OHLCV свічки з Binance REST API через httpx |
| `src/data/news_watcher.py` | Читає крипто-новини з RSS (CoinDesk, Cointelegraph) |
| `src/indicators.py` | EMA, ATR, ADX — написані вручну (без pandas-ta) |

### Сигнали

| Файл | Призначення |
|------|-------------|
| `src/signals/trend_follower.py` | Сигнал: EMA50 перетинає EMA200 + ADX > 25 |
| `src/signals/breakout.py` | Сигнал: ціна пробиває максимум/мінімум 20 свічок + об'єм 1.5x |
| `src/signals/pre_checks.py` | Фільтр правилами: тренд на 3 ТФ, R:R ≥ 2.0, kill switch |
| `src/signals/signal_engine.py` | Зводить всі генератори, запускає pre_checks |

### Ризик

| Файл | Призначення |
|------|-------------|
| `src/risk/sizing.py` | Розмір позиції: `(баланс × 1%) / відстань_до_стопу` |

### AI фільтр

| Файл | Призначення |
|------|-------------|
| `src/ai/hermes_filter.py` | Основний AI: Hermes 3 (локально через Ollama) |
| `src/ai/claude_filter.py` | Запасний AI: Claude Sonnet через Anthropic API |
| `src/ai/prompts.py` | Системний промпт (12 пунктів чеклісту) + контекст угоди |

### Виконання

| Файл | Призначення |
|------|-------------|
| `src/execution/paper_executor.py` | Відкриває/закриває паперові позиції, рахує баланс |
| `src/execution/portfolio.py` | Моніторить відкриті позиції кожен тік |

### Аналітика

| Файл | Призначення |
|------|-------------|
| `src/database/models.py` | SQLAlchemy моделі: Trade, Candle, NewsItem, DailyStats |
| `src/database/db.py` | Async SQLite підключення (aiosqlite) |
| `src/analytics/journal.py` | Записує відкриття/закриття угод в БД |
| `src/analytics/metrics.py` | Статистика: win rate, Sharpe ratio, max drawdown |

### Сповіщення

| Файл | Призначення |
|------|-------------|
| `src/notifications/telegram.py` | Відправляє повідомлення в Telegram при сигналах |

---

## Як запустити

```powershell
cd C:\Users\homePC\Desktop\trade_bot
py orchestrator.py
```

Або у фоні через термінал.

---

## Параметри (.env)

```env
EXCHANGE=binance
TELEGRAM_BOT_TOKEN=...         # токен бота
TELEGRAM_CHAT_ID=...           # твій chat ID

PAPER_TRADING=true             # симуляція (не реальні гроші!)
INITIAL_BALANCE=10000          # стартовий баланс в USDT
RISK_PER_TRADE=0.01            # ризик 1% від балансу на угоду
MAX_LEVERAGE=5
MAX_POSITIONS=3                # максимум 3 відкритих позиції
MAX_SAME_DIRECTION=2           # максимум 2 лонги або 2 шорти

WATCHLIST=BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT

# Kill switches — автоматичне зупинення торгівлі
MAX_DAILY_DRAWDOWN=0.03        # -3% за день → стоп
MAX_WEEKLY_DRAWDOWN=0.10       # -10% за тиждень → стоп
MAX_MONTHLY_DRAWDOWN=0.15      # -15% за місяць → стоп
MAX_LOSS_STREAK=2              # 2 збитки підряд → стоп

OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=hermes3:latest
```

---

## Логіка торгівлі крок за кроком

### 1. Генерація сигналу

**TrendFollower:**
- EMA50 перетинає EMA200 знизу вгору (лонг) або зверху вниз (шорт)
- ADX > 25 (є сильний тренд)
- Перевіряється на 1h таймфреймі

**BreakoutGenerator:**
- Ціна закрилась вище максимуму останніх 20 свічок (лонг)
- Або нижче мінімуму 20 свічок (шорт)
- Об'єм поточної свічки ≥ 1.5× середнього об'єму

### 2. Pre-checks (правила)

- Тренд на 4h і 1d збігається з напрямком сигналу
- ATR не занадто великий (не торгуємо у хаотичному ринку)
- BTC не падає/росте більш ніж на 3% за годину
- Kill switches не спрацювали
- R:R ≥ 2.0 (потенційний прибуток мінімум вдвічі більший за ризик)
- Немає критичних новин в останні 2 години

### 3. AI фільтр (Hermes)

AI отримує повний контекст: ціна, індикатори, новини, поточний баланс, стрик — і повертає:
```json
{
  "decision": "take",        // або "reduced" або "skip"
  "size_multiplier": 1.0,   // 0.0–1.0
  "reason": "Strong trend",
  "concerns": [],
  "regime": "trending"
}
```

### 4. Відкриття позиції

- Розмір: `(10000 × 1% × size_multiplier) / відстань_до_SL`
- Stop Loss: 1.5 × ATR нижче/вище ціни входу
- TP1: +1R (закрити 50% позиції, пересунути SL на беззбиток)
- TP2: +2R (закрити ще 25%)
- Runner: решта 25% з трейлінг стопом (1 ATR)

### 5. Моніторинг позиції

Кожен тік (60 сек) portfolio.py перевіряє:
- Чи спрацював SL → закрити, записати збиток
- Чи досягнуто TP1 → частково закрити, пересунути SL на вхід
- Чи досягнуто TP2 → частково закрити
- Трейлінг стоп для runner частини
- Якщо 24 години без +1R руху → примусово закрити

### 6. Telegram сповіщення

При відкритті угоди:
```
SIGNAL: BTC/USDT LONG
Entry: 95 420.50
SL: 93 100.00
TP1: 97 740.00 (+1R)
TP2: 100 060.00 (+2R)
Size: $430.00 | Risk: $100.00
AI: take (mult=1.00) - Strong trend alignment
```

При закритті:
```
CLOSED: BTC/USDT LONG
PnL: +$210.50 (+2.1R)
Reason: TP1 hit
```

---

## AI компоненти

### Hermes 3 (основний)
- Локальна модель, безкоштовно, без інтернету
- Запускається через Ollama на порту 11434
- Блокуючий виклик обернений в asyncio executor щоб не заморожувати головний цикл

### Claude Sonnet (запасний)
- Використовується якщо Ollama недоступна
- Потребує `ANTHROPIC_API_KEY` в `.env`

### Safe Default (останній рубіж)
- Якщо обидва AI недоступні → `reduced` з multiplier 0.5
- Бот продовжує роботу але з меншим розміром

---

## База даних (SQLite)

Файл: `trading_bot.db`

Таблиці:
- `trades` — всі угоди (відкриття, закриття, PnL, R-multiple)
- `candles` — кешовані свічки
- `news_items` — збережені новини
- `daily_stats` — денна статистика

---

## Вирішені технічні проблеми

| Проблема | Рішення |
|----------|---------|
| pandas-ta не підтримує Python 3.14 | Написали EMA/ATR/ADX вручну в `src/indicators.py` |
| ccxt + aiodns: DNS timeout на Windows | Замінили ccxt на прямі запити до Binance REST API через httpx |
| ollama.chat() блокує asyncio | Обернули в `loop.run_in_executor(None, _sync_call)` |
| pandas 2.x не компілюється на Python 3.14 | Апгрейд до pandas 3.0+ (є prebuilt wheels) |

---

## Наступні кроки (після 30 паперових угод)

1. Перевірити метрики: win rate ≥ 45%, max drawdown < 15%
2. A/B порівняння: з AI фільтром vs без
3. Якщо метрики гарні — можна підключити реальний API Binance (змінити `PAPER_TRADING=false`)
4. Опціонально: додати більше монет до watchlist
