# Торговий бот з Hermes AI — повний опис роботи

## Загальна архітектура

```
Ринкові дані (Binance)
        ↓
  PriceCollector          NewsWatcher
  30 монет × 3 TF         CryptoPanic RSS
        ↓                      ↓
         SignalEngine (кожні 5 хв)
         TrendFollower + Breakout
                ↓
          rule_pre_checks
         (10 правил, < 1мс)
                ↓
          HermesFilter ← Пам'ять
           (Ollama AI)
                ↓
         PaperExecutor
         (відкрити угоду)
                ↓
          Portfolio
       (моніторинг 1/хв)
                ↓
     SL / TP1 / TP2 / Trailing / Time
                ↓
        Telegram + Dashboard
```

---

## 1. Збір даних

**PriceCollector** (`src/data/price_collector.py`) кожну хвилину завантажує свічки для 30 монет у трьох таймфреймах: `1h`, `4h`, `1d`. Монети беруться з `WATCHLIST` в `.env`. Дані зберігаються в пам'яті як словник `{coin: {tf: DataFrame}}`.

**NewsWatcher** (`src/data/news_watcher.py`) кожні 15 хвилин тягне новини з CryptoPanic RSS. Якщо є важлива новина (hot/negative) в останню годину — сигнали блокуються (в реальному режимі).

---

## 2. Генерація сигналів

Кожні 5 хвилин SignalEngine перевіряє всі монети через два генератори:

### TrendFollower
Шукає тренд на 4h таймфреймі:
- EMA50 перетнула EMA200 знизу вгору → **LONG**
- EMA50 перетнула EMA200 зверху вниз → **SHORT**
- ADX > 25 (тренд сильний, не флет)
- Стоп = поточна ціна − 2×ATR (для LONG)
- TP1 = entry + 1×ATR, TP2 = entry + 2×ATR

### BreakoutGenerator
Шукає прориви рівнів на 1h таймфреймі:
- Ціна пробила максимум/мінімум останніх 20 свічок
- Об'єм підтверджує (у реальному режимі)
- Стоп = рівень пробою − ATR

### Pre-checks (правила перед AI)
Після генерації кожен сигнал проходить швидку перевірку (~1 мс):

| Перевірка | Реальний режим | Test mode |
|-----------|---------------|-----------|
| 1d тренд узгоджений з напрямком | ✅ | пропускається |
| 4h тренд узгоджений | ✅ | пропускається |
| ADX(4h) > 25 | ✅ | пропускається |
| ATR не екстремальний | ✅ | пропускається |
| BTC не рухається > 2%/год | ✅ | пропускається |
| Важливих новин немає | ✅ | пропускається |
| Денна просадка < 3% | ✅ | ✅ |
| Loss streak < 5 | ✅ | ✅ |
| Відкритих позицій < 15 | ✅ | ✅ |
| Однонапрямкових < 8 | ✅ | ✅ |
| R:R ≥ 1.4 (TP2 vs стоп) | ✅ | ✅ |

Сигнали що пройшли — йдуть до Hermes. Що не пройшли — логуються і відправляються в Telegram як "сигнал пропущено".

---

## 3. Hermes — як працює AI

Hermes — це локальна мовна модель **Hermes 3 8B** (Llama 3.1 тюнінг) яка запускається через **Ollama** на RTX 3070 Ti. Вся обробка — локально, без cloud, без платіжок.

### 3.1 Роль Hermes

Hermes — **risk manager**, не трейдер. Він не шукає сигнали — він їх перевіряє. Отримавши сигнал, Hermes відповідає на одне питання:

> "Чи варто входити в цю угоду зараз, з урахуванням всього контексту?"

Три можливих відповіді:
- **`take`** — входити з повним розміром (size_multiplier = 1.0)
- **`reduced`** — входити з меншим розміром (0.25–0.75)
- **`skip`** — пропустити угоду

### 3.2 Що отримує Hermes (контекст)

Перед кожним рішенням Hermes отримує структурований текст:

```
СИГНАЛ:
- Coin: SOL/USDT
- Direction: LONG
- Entry: 142.30
- Suggested stop: 138.45
- Suggested TP1: 146.15
- Suggested TP2: 150.00
- Confidence: 0.72
- Reason: EMA50 crossed above EMA200, ADX=31.4

КОНТЕКСТ МОНЕТИ:
- SOL price now: 142.30
- SOL 1d trend: bull
- SOL 4h trend: bull
- SOL 1h trend: bull
- ATR(14, 1h): 1.8540
- ADX(4h): 31.4

КОНТЕКСТ BTC:
- BTC 1h change: +0.23%
- BTC 4h trend: bull

FUNDING та ЛІКВІДАЦІЇ:
- SOL funding rate: 0.0100% / 8h
- SOL liquidations 1h: $0

НОВИНИ (24h):
- Solana network processes record 2.1M TPS
- Bitcoin ETF sees $240M inflows today

МІЙ ДОСВІД (схожі минулі угоди):
✅ SOL/USDT LONG | take(1.0) | bull_trend ADX=28 | +1.8R | Урок: В bull тренді з ADX>25 SOL добре тримає хід до TP2
❌ SOL/USDT LONG | take(1.0) | chop ADX=18 | -1.0R | Урок: В chop ринку краще skip або reduced навіть якщо EMA перетин є

СТАН БОТА:
- Drawdown today: -0.8%
- Loss streak: 1
- Win rate (last 30): 58.0%
- Avg R (last 30): +0.42
- Open positions: 3/15
```

### 3.3 Системний промпт (правила для Hermes)

Hermes завжди бачить системний промпт:

```
Ти консервативний risk manager криптофонду.

CHECK LIST (всі повинні бути ✅):
- 1d тренд узгоджений з напрямком?
- 4h тренд узгоджений?
- ADX(4h) > 25?
- Точка входу — pullback до структури?
- Стоп у логічному місці?
- TP ≥ 1.5× стоп-дистанції?
- ATR не екстремальний?
- BTC спокійний (<2%/год)?
- Немає важливих новин?
- Денна просадка <3%?
- Loss streak <2?
- Макс 2 однонапрямкові позиції?

ЯКЩО ВСІ ✅ → take
ЯКЩО 1-2 ⚠️ → reduced (25-50%)
ЯКЩО 3+ ⚠️ → skip

RED FLAGS (одна = skip):
- Режим = panic
- BTC впав >5% за годину
```

### 3.4 Відповідь Hermes (завжди JSON)

Hermes відповідає строго валідним JSON (параметр `format="json"` в Ollama):

```json
{
  "decision": "take",
  "size_multiplier": 0.8,
  "reason": "Bull тренд на всіх TF, ADX сильний. Невеликий стрік збитків — reduced для обережності.",
  "concerns": ["loss streak = 1"],
  "regime": "bull_trend"
}
```

Після отримання відповіді:
1. Якщо `decision = skip` → угода не відкривається, в Telegram "Сигнал пропущено"
2. Якщо `take` або `reduced` → розраховується розмір позиції через `size_multiplier`

### 3.5 Розмір позиції

```python
risk_per_trade = balance × 0.02      # 2% від балансу в ризику
risk_usd = risk_per_trade × size_multiplier
stop_distance_pct = |entry - stop| / entry
size_usd = risk_usd / stop_distance_pct
```

Тобто: якщо стоп = 3% від входу і ризик $200 → розмір = $200 / 0.03 = **$6666**. Якщо Hermes каже `reduced(0.5)` → розмір = **$3333**.

---

## 4. Пам'ять Hermes (Hermes Memory System)

Це найважливіша частина. Hermes навчається на власних угодах. Цикл:

```
Угода відкрита → save_decision()  → зберегти що бачив і що вирішив
Угода закрита → update_outcome() → записати результат (win/loss/breakeven)
               → generate_lesson() → Hermes аналізує своє рішення
Наступний сигнал → get_relevant_memories() → показати Hermesу досвід
```

### 4.1 save_decision() — при відкритті

Зберігає в таблицю `hermes_memory`:
- Монета, напрям, джерело сигналу
- Режим ринку (bull/bear/chop/panic)
- ADX(4h) — сила тренду
- BTC тренд
- Рішення Hermes: take/reduced/skip
- size_multiplier
- Причина рішення (текст)

### 4.2 update_outcome() — при закритті

Записує результат:
- `win` якщо R > +0.1
- `loss` якщо R < -0.1
- `breakeven` якщо між -0.1 і +0.1
- Реальний R-multiple (наприклад +2.1R або -1.0R)

### 4.3 generate_lesson() — урок від Hermes

Після запису результату бот асинхронно запитує Hermes:

```
Ти щойно закрив угоду. Проаналізуй своє рішення.

Монета: SOL/USDT LONG
Твоє рішення: take (size_multiplier=1.0)
Твоя причина тоді: Bull тренд узгоджений на всіх TF
Режим ринку: bull_trend
ADX(4h): 31.4
BTC тренд: bull

Результат: +1.82R (прибуток), статус: CLOSED_TP2

Сформулюй урок в 1-2 реченнях: що ти зробив правильно або де помилився?
```

Hermes відповідає JSON з уроком:
```json
{"lesson": "В bull тренді з ADX>28 та позитивним BTC — повний розмір виправданий. TP2 досягнуто без pullback."}
```

Урок зберігається в БД назавжди.

### 4.4 get_relevant_memories() — пошук досвіду

Перед кожним новим рішенням Hermes отримує до 6 найрелевантніших спогадів:

**Алгоритм скорингу:**
```python
score = 0
if memory.coin == current_coin:      score += 3  # та сама монета важливіша
if memory.direction == current_dir:  score += 2  # той самий напрям
if memory.regime == current_regime:  score += 1  # той самий режим
```

Вибираються ТОП-6 з останніх 50 спогадів що мають і результат, і урок.

**Ефект:** Після ~20-30 угод Hermes вже має конкретний контекст. Наприклад:
- "На SOL LONG в bull режимі я 4 рази брав take — 3 виграші, 1 збиток на stoploss"
- "В chop режимі краще reduced або skip"

---

## 5. Управління позицією (Portfolio)

Після відкриття угода моніториться щохвилини:

```
current_price = остання свічка 1h

1. Оновити current_price в БД (для dashboard)
2. Стоп-лосс → закрити зі статусом CLOSED_SL
3. TP1 досягнутий → переставити стоп на breakeven (entry_price)
4. TP2 досягнутий → закрити зі статусом CLOSED_TP2
5. Після TP1: trailing stop = high_watermark - 1.5×ATR
6. Часовий вихід → якщо угода > 24h і PnL < 1R → закрити
```

### Екстрений вихід
Якщо BTC рухнув більше ніж 5% за 1 свічку → закрити всі LONG позиції негайно.

---

## 6. Баланс (Paper Trading)

```
Початок:             $10,000
Відкрита угода:      balance -= size_usd      (заблоковано)
Закрита угода +PnL:  balance += exit_value    (повернено + прибуток/збиток)

exit_value (LONG):   exit_price × (size_usd / entry_price)
exit_value (SHORT):  (2×entry - exit_price) × (size_usd / entry_price)
```

Після рестарту бот відновлює стан з БД через `restore_from_db()`.

---

## 7. Дашборд (http://localhost:8080)

Оновлюється кожні 10 секунд. Показує:

| Карта | Що відображає |
|-------|--------------|
| Вільний баланс | $10000 + закрит.PnL − заблоковано |
| В позиціях | Скільки заблоковано + Float PnL поточний |
| Загальний капітал | INITIAL + закрит.PnL + нереаліз.PnL |
| PnL зараз | Реалізований + Float, розбивка в підписі |

**Float PnL** (нереалізований) рахується з поля `current_price` в БД — portfolio оновлює його щохвилини.

Таблиця відкритих позицій показує: вхід, **поточну ціну**, стоп, TP1/TP2, розмір, **Float PnL** кожної позиції.

---

## 8. Telegram сповіщення

| Подія | Повідомлення |
|-------|-------------|
| Відкрита угода | Монета, напрям, вхід, стоп, TP1/TP2, розмір, рішення Hermes, вільний баланс |
| Закрита угода | Причина, вихід, PnL в USD і R, баланс після |
| Сигнал пропущено | Монета, причини (pre-checks або Hermes skip) |
| Kill switch | Денна просадка або BTC crash |
| Денний звіт | Статистика за день о 00:00 UTC |

---

## 9. Конфігурація (.env)

```env
PAPER_TRADING=true          # false тільки після валідації
TEST_SIGNALS=true           # Спрощені умови для тестування
INITIAL_BALANCE=10000
RISK_PER_TRADE=0.02         # 2% на угоду

OLLAMA_MODEL=hermes3:latest
OLLAMA_HOST=http://host.docker.internal:11434

MAX_POSITIONS=15
MAX_SAME_DIRECTION=8
MAX_DAILY_DRAWDOWN=0.03     # 3% денна просадка = стоп
MAX_LOSS_STREAK=5
TIME_EXIT_HOURS=24

WATCHLIST=BTC/USDT,ETH/USDT,SOL/USDT,...  # 30 монет
```

---

## 10. Файлова структура

```
trade_bot/
├── orchestrator.py          ← головний процес, головний цикл
├── dashboard.py             ← FastAPI дашборд (порт 8080)
├── src/
│   ├── config.py            ← всі налаштування з .env
│   ├── ai/
│   │   ├── hermes_filter.py ← виклик Ollama, fallback на Claude
│   │   ├── hermes_memory.py ← пам'ять: save/update/generate/get
│   │   └── prompts.py       ← системний промпт + build_trade_context()
│   ├── signals/
│   │   ├── signal_engine.py ← агрегатор генераторів
│   │   ├── trend_follower.py← EMA cross + ADX стратегія
│   │   ├── breakout.py      ← прорив рівнів
│   │   └── pre_checks.py    ← 10 правил до AI
│   ├── execution/
│   │   ├── paper_executor.py← відкрити/закрити в paper режимі
│   │   └── portfolio.py     ← SL/TP/trailing/time/emergency
│   ├── data/
│   │   ├── price_collector.py← Binance CCXT, 30 монет × 3 TF
│   │   └── news_watcher.py  ← CryptoPanic RSS
│   ├── database/
│   │   ├── models.py        ← Trade, HermesMemory, Candle, News
│   │   └── db.py            ← SQLite WAL + async SQLAlchemy
│   ├── analytics/
│   │   ├── journal.py       ← record_open / record_close
│   │   └── metrics.py       ← win rate, Sharpe, profit factor
│   ├── notifications/
│   │   └── telegram.py      ← повідомлення українською
│   └── risk/
│       └── sizing.py        ← розмір позиції через R
├── data/
│   └── trading.db           ← SQLite база (trades, hermes_memory, ...)
├── logs/
│   └── bot_YYYY-MM-DD.log
└── docker-compose.yml
```

---

## 11. Запуск

```powershell
# Запуск всього одною командою
docker compose up --build -d

# Логи бота в реальному часі
docker compose logs -f tradebot

# Дашборд
# Відкрий у браузері: http://localhost:8080

# Зупинка
docker compose down
```

---

## Ключова ідея

Більшість торгових ботів — детерміновані: якщо умова X → купити. Наш бот відрізняється тим що **Hermes бачить контекст**, а не просто умови. Він враховує:
- Що робить BTC прямо зараз
- Яка послідовність збитків
- Які новини були в останні 24 години
- **Що він сам робив в схожих ситуаціях і що з того вийшло**

Останній пункт — ключовий. Після 30-50 угод Hermes починає ухвалювати рішення на основі реального досвіду в цій конкретній ринковій конфігурації, а не тільки правил.
