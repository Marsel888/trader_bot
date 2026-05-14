# Торговий Бот — Прогрес і Плани

## Що реалізовано

### Архітектура
- **Оркестратор** (`orchestrator.py`) — головний цикл: tick кожні 5с
- **Price Collector** — REST candles з Binance Futures (fapi) кожні 60с
- **WS Price Stream** — real-time ціни з Binance Futures WebSocket (~1с)
- **Signal Engine** — правило-based сигнали (тренд + breakout)
- **AI Market Scanner** — Hermes 4 70B через OpenRouter сканує всі 30 монет
- **Portfolio** — стоп/TP/trailing/time-based виходи кожні 5с
- **Paper Executor** — симуляція угод без реального exchange
- **Dashboard** — FastAPI + live ціни через Binance REST polling
- **Telegram Bot** — сповіщення + команди /history /pnl /positions

### AI система
- **Agent A** (Hermes/Технічний): OpenRouter Hermes 4 70B, аналізує RSI/ADX/EMA/ATR
- **Agent B** (Volume/Momentum): rule-based, перевіряє об'єм і momentum
- **Agent C** (Regime): rule-based, перевіряє BTC тренд і market breadth
- **Консенсус**: 2 з 3 агентів мають схвалити → угода відкривається
- **Hermes Exit**: AI перевіряє відкриті позиції при тригерах (BTC рух, новини, stalling)
- **Hermes Memory**: зберігає рішення і уроки після кожної угоди

### Ризик-менеджмент
- 1% ризику на угоду, плече 5-7x
- Стоп-лосс (фіксований), TP1 (breakeven), TP2 (trailing)
- Kill switch: daily drawdown -15%, loss streak 10
- Emergency exit: BTC рухнув >5% проти лонгів

### Інфраструктура
- Docker Compose (tradebot + dashboard контейнери)
- SQLite DB (угоди, свічки, новини, HermesMemory)
- Shared volume для live_prices.json
- GitHub: github.com/Marsel888/trader_bot

---

## Виправлено (2026-05-14)

1. **Ollama → OpenRouter** в `hermes_exit.py` і `hermes_memory.py`
2. **-1.00R баг** — формула R тепер використовує `risk_usd` (фіксований при відкритті), а не поточний `stop_price` (який змінюється trailing/breakeven)
3. **Telegram постійні кнопки** — ReplyKeyboardMarkup завжди видима внизу чату
4. **UTC мітки** — час на дашборді тепер показує UTC явно
5. **Консенсус 2/3** — додані volume_agent і regime_agent

---

## Що потрібно зробити

### Пріоритет 1 (критичне)
- [ ] **Валідація стратегії** — зібрати мінімум 50 угод і перевірити win rate, avg R, profit factor
- [ ] **Реальний API ключ OpenRouter** — поточний ключ був скомпрометований (показаний в чаті)
- [ ] **Реальний Telegram токен** — поточний токен був показаний в чаті, замінити

### Пріоритет 2 (покращення точності)
- [ ] **Калібрування консенсусу** — можливо volume_agent занадто жорсткий, зібрати статистику
- [ ] **Додати VWAP** до indicators.py для volume_agent
- [ ] **Funding rate** — враховувати funding rate при вході (Binance Futures)
- [ ] **Backtesting** — протестувати стратегію на історичних даних перед live

### Пріоритет 3 (функціональність)
- [ ] **Telegram /stop** команда — зупинити бота без SSH
- [ ] **Конвертація UTC→local** в дашборді (вибір timezone)
- [ ] **Графіки PnL** на дашборді (Chart.js)
- [ ] **Alert при великому плавучому збитку** в Telegram
- [ ] **Webhook замість polling** для Telegram (для production)

### Пріоритет 4 (live trading)
- [ ] Підключити реальний Binance API ключ
- [ ] Перевірити мінімальні розміри позицій по кожній монеті
- [ ] Налаштувати PAPER_TRADING=false тільки після 100+ паперових угод з позитивним результатом

---

## Метрики для live trading (цілі)
- Win rate ≥ 45%
- Avg R ≥ +0.5R
- Profit factor ≥ 1.3
- Max drawdown < 15%
- Мінімум 50 паперових угод
