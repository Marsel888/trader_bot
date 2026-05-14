# Торговий Бот — Прогрес і Плани

## Архітектура системи

```
Binance Futures API (REST + WebSocket)
         ↓
   Price Collector (60с) + WS Stream (1с)
         ↓
   Signal Engine (rule-based, кожні 5хв)
         ↓
   AI Market Scanner (Agent A — Hermes 4 70B)
         ↓
   ┌─────────────────────────────────┐
   │        Консенсус 2/3            │
   │  A: Hermes 4 70B (технічний)   │
   │  B: DeepSeek Chat (об'єм)      │
   │  C: Qwen 2.5 72B (макро)       │
   └─────────────────────────────────┘
         ↓ (≥2 голоси "take")
   Paper Executor → відкриття угоди
         ↓
   Portfolio (стоп/TP/trailing, кожні 5с)
         ↓
   Telegram + Dashboard
```

---

## Що реалізовано

### Дані і ціни
- **Binance Futures REST** (`fapi.binance.com`) — candles 1h/4h/1d кожні 60с
- **Binance Futures WebSocket** (`fstream.binance.com`) — real-time ціни ~1с
- **Live prices** — браузер polling `fapi.binance.com/fapi/v1/ticker/price` кожні 3с
- **live_prices.json** — бот пише кожну 1с, дашборд читає як fallback
- **1000x символи** — SHIB/PEPE правильно маппляться на `1000SHIBUSDT`

### Сигнали
- **Trend Follower** — EMA50/200 + ADX + ATR фільтри
- **Breakout** — пробій рівнів з об'ємом
- **AI Market Scanner** — Hermes 4 70B сам сканує всі 30 монет і знаходить можливості
- **Signal Engine** — об'єднує всі сигнали, перевіряє rule-based фільтри

### AI система (3 незалежних агенти)
| Агент | Модель | Аналізує |
|-------|--------|----------|
| A — Технічний | `nousresearch/hermes-4-70b` | RSI, ADX, EMA, ATR, тренд |
| B — Об'єм | `deepseek/deepseek-chat` | Volume, momentum, price-volume |
| C — Макро | `qwen/qwen-2.5-72b-instruct` | BTC тренд, market breadth |

- **Консенсус**: потрібно 2/3 голосів "take" → угода відкривається
- **Hermes Exit**: перевіряє відкриті позиції при тригерах (BTC рух >2%, негативні новини, позиція стоїть >12h)
- **Hermes Memory**: зберігає рішення і уроки після кожної угоди (навчання)
- **Провайдер**: OpenRouter (один API ключ, різні моделі)

### Ризик-менеджмент
- 1% ризику на угоду
- Плече: BTC/ETH→7x, топ альти→6x, решта→5x
- **TP1** → breakeven (стоп переноситься на вхід)
- **TP2** → trailing stop (2× ATR)
- **Kill switch**: daily drawdown -15%, loss streak 10
- **Emergency exit**: BTC рухнув >5% за хвилину

### Виконання
- **Paper Executor** — симуляція без реальних ордерів
- **restore_from_db()** — відновлює відкриті позиції після перезапуску
- **Retroactive check** — перевіряє чи спрацював SL/TP поки бот був офлайн

### Дашборд (`http://server:8080`)
- Баланс, PnL, відкриті позиції, закриті угоди
- Live ціни для всіх 30 монет (оновлення кожні 3с)
- Логи бота в реальному часі
- Кнопка скидання (для тестування)
- Час відображається в UTC

### Telegram бот
- Сповіщення про відкриття/закриття угод
- Постійна клавіатура: 📋 Історія / 💰 PnL / 📍 Позиції
- Команди: `/start` `/history` `/pnl` `/positions`
- Денний звіт о 00:00 UTC

### Інфраструктура
- **Docker Compose**: 2 контейнери (tradebot + dashboard)
- **SQLite**: угоди, свічки, новини, HermesMemory
- **GitHub**: `github.com/Marsel888/trader_bot`
- **Ubuntu сервер**: `~/trade_bot/`

---

## Виправлено

| Дата | Проблема | Рішення |
|------|----------|---------|
| 2026-05-12 | Ціни з Binance spot (відрізняються від TradingView) | → Binance Futures API |
| 2026-05-12 | Негативний вільний баланс на дашборді | Locked = margin, не notional |
| 2026-05-12 | Kill switch -60% (хибний) | Drawdown від total_equity (free+locked) |
| 2026-05-12 | SHIB/PEPE 400 Bad Request | FUTURES_SYMBOL_OVERRIDE (1000x) |
| 2026-05-12 | Ollama блокує старт (`Waiting for Ollama`) | Видалено Ollama, тільки OpenRouter |
| 2026-05-12 | `get_relevant_memories()` помилка | Параметр `regime` став опціональним |
| 2026-05-13 | Ціни не оновлювались в реал-тайм | Browser polling Binance REST кожні 3с |
| 2026-05-14 | Ollama в `hermes_exit.py` і `hermes_memory.py` | → OpenRouter |
| 2026-05-14 | `-1.00R` при прибутковій угоді | R = pnl / risk_usd (не trailing stop) |
| 2026-05-14 | Telegram кнопки зникали | ReplyKeyboardMarkup (постійна) |
| 2026-05-14 | Rule-based агенти B і C | → Справжній AI (DeepSeek, Qwen) |
| 2026-05-14 | Всі агенти одна модель | Кожен агент — окрема модель |

---

## Що потрібно зробити

### Пріоритет 1 — Безпека
- [ ] Замінити OpenRouter API ключ (старий потрапив в чат)
- [ ] Замінити Telegram токен (старий потрапив в чат)

### Пріоритет 2 — Валідація стратегії
- [ ] Зібрати 50+ угод і перевірити: win rate ≥45%, avg R ≥+0.5R, profit factor ≥1.3
- [ ] Порівняти результати з консенсусом vs без (A/B тест)
- [ ] Калібрувати пороги агентів B і C якщо занадто жорсткі

### Пріоритет 3 — Покращення
- [ ] **Backtesting** — перевірити стратегію на 6-12 місяцях даних
- [ ] **Funding rate** — пропускати угоди при від'ємному funding
- [ ] **VWAP** — додати в indicators.py для агента B
- [ ] **Графіки PnL** на дашборді (Chart.js)
- [ ] **Конвертація UTC→local** в дашборді
- [ ] **Alert** в Telegram при floating loss > X%

### Пріоритет 4 — Live trading
- [ ] Перевірити мінімальні розміри позицій по кожній монеті
- [ ] Тест на малому балансі ($100) з реальним API
- [ ] `PAPER_TRADING=false` тільки після 100+ паперових угод з позитивним результатом

---

## Метрики для переходу на live

| Метрика | Ціль |
|---------|------|
| Win rate | ≥ 45% |
| Avg R | ≥ +0.5R |
| Profit factor | ≥ 1.3 |
| Max drawdown | < 15% |
| Кількість угод | ≥ 100 |
