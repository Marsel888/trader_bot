"""
Локальний веб-дашборд торгового бота.
Відкрий у браузері: http://localhost:8080

Запуск:
    py dashboard.py
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import uvicorn
from sqlalchemy import select, delete, func

from src.database.db import init_db, AsyncSessionLocal
from src.database.models import Trade, TradeStatus, Candle
from src.config import cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Торговий Бот", lifespan=lifespan)
LOG_DIR = Path("logs")


@app.post("/api/reset")
async def api_reset():
    """Delete all trades and reset to initial state."""
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Trade))
        await session.commit()
    return {"ok": True, "message": f"Всі угоди видалено. Баланс скинуто до ${cfg.INITIAL_BALANCE:.0f}"}


@app.get("/api/status")
async def api_status():
    alive = False
    last_seen = None
    if LOG_DIR.exists():
        logs = sorted(LOG_DIR.glob("bot_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            age = time.time() - logs[0].stat().st_mtime
            alive = age < 120
            last_seen = datetime.fromtimestamp(logs[0].stat().st_mtime).strftime("%H:%M:%S")
    return {"alive": alive, "last_seen": last_seen}


@app.get("/api/stats")
async def api_stats():
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Trade).where(Trade.status != TradeStatus.OPEN).order_by(Trade.closed_at.desc())
        )
        closed = res.scalars().all()

        res2 = await session.execute(
            select(Trade).where(Trade.status == TradeStatus.OPEN).order_by(Trade.created_at.desc())
        )
        open_trades = res2.scalars().all()

        # Latest 1h candle price per coin from Candle table (updated every 60s by bot)
        live_prices: dict[str, float] = {}
        if open_trades:
            coins = list({t.coin for t in open_trades})
            for coin in coins:
                row = await session.execute(
                    select(Candle.close)
                    .where(Candle.coin == coin, Candle.timeframe == "1h")
                    .order_by(Candle.timestamp.desc())
                    .limit(1)
                )
                val = row.scalar()
                if val:
                    live_prices[coin] = float(val)

    closed_pnl = sum(t.pnl_usd or 0 for t in closed)
    locked = sum(t.size_usd / (t.leverage or 5) for t in open_trades)

    def _float_pnl(t) -> float:
        # Priority: live candle price → portfolio current_price → entry
        cur = live_prices.get(t.coin) or t.current_price or t.entry_price
        coins = t.size_usd / t.entry_price if t.entry_price else 0
        if t.direction == "LONG":
            return (cur - t.entry_price) * coins
        return (t.entry_price - cur) * coins

    unrealized_pnl = sum(_float_pnl(t) for t in open_trades)

    free_balance = cfg.INITIAL_BALANCE + closed_pnl - locked
    total_equity = cfg.INITIAL_BALANCE + closed_pnl + unrealized_pnl

    total = len(closed)
    wins = sum(1 for t in closed if (t.r_multiple or 0) > 0)
    losses = total - wins
    avg_r = (sum(t.r_multiple for t in closed if t.r_multiple) / total) if total else 0

    streak = 0
    for t in closed:
        if (t.r_multiple or 0) <= 0:
            streak += 1
        else:
            break

    status_ua = {
        "CLOSED_SL": "Стоп-лосс",
        "CLOSED_TP2": "Тейк-профіт",
        "CLOSED_TRAILING": "Трейлінг",
        "CLOSED_TIME": "Час (24h)",
        "CLOSED_EMERGENCY": "Аварійний",
    }

    return {
        "free_balance": round(free_balance, 2),
        "locked": round(locked, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "realized_pnl": round(closed_pnl, 2),
        "total_equity": round(total_equity, 2),
        "total_pnl": round(closed_pnl + unrealized_pnl, 2),
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 1) if total else 0,
        "avg_r": round(avg_r, 2),
        "loss_streak": streak,
        "open_count": len(open_trades),
        "open_trades": [
            {
                "id": t.id,
                "coin": t.coin,
                "direction": t.direction,
                "entry": t.entry_price,
                "cur": round(live_prices.get(t.coin) or t.current_price or t.entry_price, 4),
                "stop": t.stop_price,
                "tp1": t.tp1_price,
                "tp2": t.tp2_price,
                "size": round(t.size_usd, 0),
                "margin": round(t.size_usd / (t.leverage or 5), 0),
                "leverage": t.leverage or 5,
                "float_pnl": round(_float_pnl(t), 2),
                "tp1_hit": t.tp1_hit,
                "ai": t.ai_decision,
                "opened": (t.created_at.strftime("%d.%m %H:%M") + " UTC") if t.created_at else "-",
            }
            for t in open_trades
        ],
        "recent_trades": [
            {
                "id": t.id,
                "coin": t.coin,
                "direction": t.direction,
                "status": status_ua.get(t.status, t.status),
                "size": round(t.size_usd, 0),
                "pnl": round(t.pnl_usd or 0, 2),
                "r": round(t.r_multiple or 0, 2),
                "closed": (t.closed_at.strftime("%d.%m %H:%M") + " UTC") if t.closed_at else "-",
            }
            for t in closed[:20]
        ],
    }


@app.get("/api/live_prices")
async def api_live_prices():
    """Latest live prices written by bot every 1s via WS stream."""
    prices_file = Path("data/live_prices.json")
    if not prices_file.exists():
        return {"prices": {}, "ts": 0}
    try:
        return json.loads(prices_file.read_text())
    except Exception:
        return {"prices": {}, "ts": 0}


@app.get("/api/prices")
async def api_prices():
    """Latest price for each coin from Candle table."""
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Candle.coin, Candle.close, Candle.timestamp)
            .where(Candle.timeframe == "1h")
            .order_by(Candle.coin, Candle.timestamp.desc())
            .distinct(Candle.coin)
        )
        results = rows.all()
    return {
        row.coin: {"price": round(row.close, 6), "updated": row.timestamp.strftime("%H:%M")}
        for row in results
    }


@app.get("/api/logs")
async def api_logs(lines: int = 80):
    if not LOG_DIR.exists():
        return {"lines": ["Логи відсутні — бот ще не запускався."], "file": ""}
    logs = sorted(LOG_DIR.glob("bot_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not logs:
        return {"lines": ["Файли логів не знайдено."], "file": ""}
    try:
        text = logs[0].read_text(encoding="utf-8", errors="replace")
        return {"lines": text.splitlines()[-lines:], "file": logs[0].name}
    except Exception as e:
        return {"lines": [f"Помилка читання: {e}"], "file": ""}


HTML = """<!DOCTYPE html>
<html lang="uk">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Торговий Бот</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #c9d1d9; font-family: 'Courier New', monospace; font-size: 13px; }
  .header { background: #161b22; border-bottom: 1px solid #30363d; padding: 12px 20px; display: flex; align-items: center; gap: 16px; }
  .header h1 { font-size: 18px; color: #58a6ff; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
  .dot-on  { background: #3fb950; box-shadow: 0 0 6px #3fb950; }
  .dot-off { background: #f85149; box-shadow: 0 0 6px #f85149; }
  .muted { color: #8b949e; font-size: 11px; }
  .ml-auto { margin-left: auto; }
  .btn-reset { background: #3a1a1a; color: #f85149; border: 1px solid #f85149; border-radius: 4px; padding: 5px 14px; font-size: 12px; cursor: pointer; font-family: inherit; }
  .btn-reset:hover { background: #f85149; color: #fff; }

  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 16px 20px; }
  .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; padding: 0 20px 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 14px 16px; }
  .card-label { color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  .card-value { font-size: 22px; font-weight: bold; margin-top: 4px; }
  .card-sub { font-size: 11px; color: #8b949e; margin-top: 3px; }
  .green  { color: #3fb950; }
  .red    { color: #f85149; }
  .blue   { color: #58a6ff; }
  .yellow { color: #d29922; }

  .section { padding: 0 20px 16px; }
  .section-title { color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; }
  th { color: #8b949e; font-size: 11px; text-align: left; padding: 5px 8px; border-bottom: 1px solid #21262d; }
  td { padding: 5px 8px; border-bottom: 1px solid #161b22; }
  tr:hover td { background: #1c2128; }

  .badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; }
  .b-long  { background:#1a3a1a; color:#3fb950; }
  .b-short { background:#3a1a1a; color:#f85149; }
  .b-sl    { background:#3a1a1a; color:#f85149; }
  .b-tp    { background:#1a3a1a; color:#3fb950; }
  .b-trail { background:#1a2a3a; color:#58a6ff; }
  .b-time  { background:#2a2a1a; color:#d29922; }
  .b-open  { background:#1a2a3a; color:#58a6ff; }
  .tp1ok   { font-size:10px; background:#0d2d0d; color:#3fb950; padding:1px 4px; border-radius:2px; margin-left:4px; }

  .log-box { background:#010409; border:1px solid #30363d; border-radius:6px; padding:12px; height:320px; overflow-y:auto; font-size:12px; line-height:1.5; }
  .log-line { white-space:pre-wrap; word-break:break-all; }
  .l-INFO    { color:#c9d1d9; }
  .l-DEBUG   { color:#8b949e; }
  .l-WARNING { color:#d29922; }
  .l-ERROR   { color:#f85149; }
  .l-SUCCESS { color:#3fb950; }
  .empty { color:#8b949e; text-align:center; padding:20px; }
</style>
</head>
<body>

<div class="header">
  <h1>🤖 Торговий Бот</h1>
  <span class="dot" id="dot"></span>
  <span id="status-txt">перевірка...</span>
  <span class="muted" id="last-seen"></span>
  <span class="muted ml-auto" id="ws-age"></span>
  <span class="muted" id="timer">оновлення через 10с</span>
  <button class="btn-reset" onclick="resetBot()">🗑 Скинути</button>
</div>

<!-- Баланс -->
<div class="grid">
  <div class="card">
    <div class="card-label">Вільний баланс</div>
    <div class="card-value" id="free-bal">—</div>
    <div class="card-sub" id="free-sub"></div>
  </div>
  <div class="card">
    <div class="card-label">В позиціях (заблоковано)</div>
    <div class="card-value blue" id="locked">—</div>
    <div class="card-sub" id="locked-sub"></div>
  </div>
  <div class="card">
    <div class="card-label">Загальний капітал</div>
    <div class="card-value" id="equity">—</div>
    <div class="card-sub" id="equity-sub"></div>
  </div>
  <div class="card">
    <div class="card-label">PnL зараз (реаліз. + float)</div>
    <div class="card-value" id="total-pnl">—</div>
    <div class="card-sub" id="pnl-sub"></div>
  </div>
</div>

<!-- Статистика -->
<div class="grid">
  <div class="card">
    <div class="card-label">Угод всього / відкрито</div>
    <div class="card-value" id="trades-cnt">—</div>
  </div>
  <div class="card">
    <div class="card-label">Відсоток виграшів</div>
    <div class="card-value" id="winrate">—</div>
  </div>
  <div class="card">
    <div class="card-label">Середній R</div>
    <div class="card-value" id="avg-r">—</div>
  </div>
  <div class="card">
    <div class="card-label">Стрік збитків</div>
    <div class="card-value" id="streak">—</div>
  </div>
</div>

<!-- Тикер цін -->
<div class="section">
  <div class="section-title">Ринок (live)</div>
  <div id="ticker" style="display:flex;flex-wrap:wrap;gap:8px;"></div>
</div>

<!-- Відкриті позиції -->
<div class="section">
  <div class="section-title">Відкриті позиції</div>
  <table>
    <thead><tr>
      <th>#</th><th>Монета</th><th>Напрям</th><th>Вхід</th><th>Поточна</th>
      <th>Стоп</th><th>ТП1</th><th>ТП2</th>
      <th>Нотіонал</th><th>Маржа</th><th>Float PnL</th><th>ШІ</th><th>Відкрито</th>
    </tr></thead>
    <tbody id="open-body"><tr><td colspan="13" class="empty">Немає відкритих позицій</td></tr></tbody>
  </table>
</div>

<!-- Останні угоди -->
<div class="section">
  <div class="section-title">Останні закриті угоди</div>
  <table>
    <thead><tr>
      <th>#</th><th>Монета</th><th>Напрям</th><th>Причина закриття</th>
      <th>Сума $</th><th>PnL $</th><th>R</th><th>Закрито</th>
    </tr></thead>
    <tbody id="closed-body"><tr><td colspan="8" class="empty">Угод ще немає</td></tr></tbody>
  </table>
</div>

<!-- Логи -->
<div class="section">
  <div class="section-title">Логи (<span id="log-file">—</span>)</div>
  <div class="log-box" id="log-box"></div>
</div>

<script>
const INITIAL_BALANCE = __INITIAL_BALANCE__;
const WATCHLIST = __WATCHLIST__;
let secs = 10;

// ── Binance Futures real-time prices ────────────────────────────
const livePrices = {};

function fmtPrice(p) {
  if (!p) return '—';
  return p < 0.01 ? p.toFixed(6) : p < 1 ? p.toFixed(5) : p < 100 ? p.toFixed(4) : p.toFixed(2);
}

const prevPrices = {};

function updatePriceCells() {
  // Update open position rows
  document.querySelectorAll('[data-coin-price]').forEach(el => {
    const symbol = el.getAttribute('data-coin-price');
    if (livePrices[symbol] !== undefined) {
      el.textContent = fmtPrice(livePrices[symbol]);
    }
  });

  // Update ticker
  const ticker = document.getElementById('ticker');
  if (!ticker) return;
  WATCHLIST.forEach(symbol => {
    const price = livePrices[symbol];
    const prev  = prevPrices[symbol];
    prevPrices[symbol] = price;
    let el = document.getElementById('tick-' + symbol);
    if (!el) {
      el = document.createElement('div');
      el.id = 'tick-' + symbol;
      el.style.cssText = 'background:#161b22;border:1px solid #30363d;border-radius:4px;padding:4px 10px;font-size:12px;min-width:110px;text-align:center';
      ticker.appendChild(el);
    }
    const color = !prev || price === prev ? '#c9d1d9' : price > prev ? '#3fb950' : '#f85149';
    const name = symbol.replace('USDT','');
    el.innerHTML = `<span style="color:#8b949e">${name}</span> <b style="color:${color}">${fmtPrice(price)}</b>`;
  });
}

const WATCHLIST_SET = new Set(WATCHLIST);

// Binance Futures REST — запитуємо ціни кожні 3 секунди напряму
async function fetchBinancePrices() {
  try {
    const res = await fetch('https://fapi.binance.com/fapi/v1/ticker/price');
    if (!res.ok) throw new Error(res.status);
    const arr = await res.json();
    let changed = false;
    arr.forEach(item => {
      if (WATCHLIST_SET.has(item.symbol)) {
        livePrices[item.symbol] = parseFloat(item.price);
        changed = true;
      }
    });
    if (changed) updatePriceCells();
    document.getElementById('ws-age').textContent = '🟢 live';
  } catch(e) {
    // Якщо Binance недоступний — беремо з нашого сервера
    try {
      const data = await fetch('/api/live_prices').then(r => r.json());
      const ageSec = Math.round(Date.now() / 1000 - (data.ts || 0));
      document.getElementById('ws-age').textContent = `🟡 ${ageSec}с тому`;
      const prices = data.prices || {};
      let changed = false;
      Object.entries(prices).forEach(([coin, price]) => {
        const symbol = coin.replace('/', '');
        if (WATCHLIST_SET.has(symbol)) { livePrices[symbol] = price; changed = true; }
      });
      if (changed) updatePriceCells();
    } catch(_) {
      document.getElementById('ws-age').textContent = '🔴 офлайн';
    }
  }
}

setInterval(fetchBinancePrices, 3000);
fetchBinancePrices();

function pc(v) { return v >= 0 ? 'green' : 'red'; }
function fmt(v) { return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2); }

function dirBadge(d) {
  return d === 'LONG'
    ? '<span class="badge b-long">ЛОНГ</span>'
    : '<span class="badge b-short">ШОРТ</span>';
}

function statusBadge(s) {
  const map = {
    'Стоп-лосс': 'b-sl', 'Тейк-профіт': 'b-tp',
    'Трейлінг': 'b-trail', 'Час (24h)': 'b-time', 'Аварійний': 'b-sl',
  };
  return `<span class="badge ${map[s]||'b-open'}">${s}</span>`;
}

async function refresh() {
  try {
    const [sr, st, lg] = await Promise.all([
      fetch('/api/status').then(r=>r.json()),
      fetch('/api/stats').then(r=>r.json()),
      fetch('/api/logs?lines=80').then(r=>r.json()),
    ]);

    // Статус
    const dot = document.getElementById('dot');
    dot.className = 'dot ' + (sr.alive ? 'dot-on' : 'dot-off');
    document.getElementById('status-txt').textContent = sr.alive ? 'Працює' : 'Зупинено';
    document.getElementById('last-seen').textContent = sr.last_seen ? `лог: ${sr.last_seen}` : '';

    // Баланс
    const realPnl = st.realized_pnl;
    const floatPnl = st.unrealized_pnl;
    const totalPnl = st.total_pnl;
    const pnlPct = (totalPnl / INITIAL_BALANCE * 100).toFixed(1);

    document.getElementById('free-bal').innerHTML = `<span class="${pc(st.free_balance - INITIAL_BALANCE)}">$${st.free_balance.toFixed(2)}</span>`;
    document.getElementById('free-sub').textContent = 'доступно для нових угод';

    document.getElementById('locked').textContent = `$${st.locked.toFixed(2)}`;
    document.getElementById('locked-sub').innerHTML = `${st.open_count} позиц. | float: <span class="${pc(floatPnl)}">${fmt(floatPnl)}</span>`;

    document.getElementById('equity').innerHTML = `<span class="${pc(totalPnl)}">$${st.total_equity.toFixed(2)}</span>`;
    document.getElementById('equity-sub').textContent = `початок: $${INITIAL_BALANCE}`;

    document.getElementById('total-pnl').innerHTML = `<span class="${pc(totalPnl)}">${fmt(totalPnl)}</span>`;
    document.getElementById('pnl-sub').innerHTML = `реаліз: <b>${fmt(realPnl)}</b> | float: <span class="${pc(floatPnl)}">${fmt(floatPnl)}</span> | ${pnlPct}%`;

    // Статистика
    document.getElementById('trades-cnt').textContent = `${st.total_trades} / ${st.open_count}`;
    document.getElementById('winrate').innerHTML = `<span class="${st.win_rate>=50?'green':'red'}">${st.win_rate}%</span>`;
    document.getElementById('avg-r').innerHTML = `<span class="${pc(st.avg_r)}">${st.avg_r >= 0 ? '+' : ''}${st.avg_r}R</span>`;
    const sk = st.loss_streak;
    document.getElementById('streak').innerHTML = `<span class="${sk>=3?'red':sk>=1?'yellow':'green'}">${sk}</span>`;

    // Відкриті позиції
    const ob = document.getElementById('open-body');
    if (!st.open_trades.length) {
      ob.innerHTML = '<tr><td colspan="13" class="empty">Немає відкритих позицій</td></tr>';
    } else {
      ob.innerHTML = st.open_trades.map(t => `<tr>
        <td>${t.id}</td>
        <td><b>${t.coin}</b></td>
        <td>${dirBadge(t.direction)}</td>
        <td>${t.entry.toFixed(4)}</td>
        <td class="blue" data-coin-price="${t.coin.replace('/','')}">${t.cur < 1 ? t.cur.toFixed(6) : t.cur < 100 ? t.cur.toFixed(4) : t.cur.toFixed(2)}</td>
        <td class="red">${t.stop.toFixed(4)}</td>
        <td class="green">${t.tp1.toFixed(4)}${t.tp1_hit?'<span class="tp1ok">BE</span>':''}</td>
        <td class="green">${t.tp2.toFixed(4)}</td>
        <td class="muted">$${t.size}×${t.leverage}</td>
        <td><b>$${t.margin}</b></td>
        <td class="${pc(t.float_pnl)}">${fmt(t.float_pnl)}</td>
        <td>${t.ai||'—'}</td>
        <td>${t.opened}</td>
      </tr>`).join('');
    }

    // Закриті угоди
    const cb = document.getElementById('closed-body');
    if (!st.recent_trades.length) {
      cb.innerHTML = '<tr><td colspan="8" class="empty">Угод ще немає</td></tr>';
    } else {
      cb.innerHTML = st.recent_trades.map(t => `<tr>
        <td>${t.id}</td>
        <td><b>${t.coin}</b></td>
        <td>${dirBadge(t.direction)}</td>
        <td>${statusBadge(t.status)}</td>
        <td>$${t.size}</td>
        <td class="${pc(t.pnl)}">${fmt(t.pnl)}</td>
        <td class="${pc(t.r)}">${t.r >= 0 ? '+' : ''}${t.r.toFixed(2)}R</td>
        <td>${t.closed}</td>
      </tr>`).join('');
    }

    // Логи
    document.getElementById('log-file').textContent = lg.file || '—';
    const box = document.getElementById('log-box');
    box.innerHTML = (lg.lines||[]).map(line => {
      let cls = 'l-INFO';
      if (line.includes('| DEBUG'))   cls = 'l-DEBUG';
      if (line.includes('| WARNING')) cls = 'l-WARNING';
      if (line.includes('| ERROR'))   cls = 'l-ERROR';
      if (line.includes('| SUCCESS')) cls = 'l-SUCCESS';
      return `<div class="log-line ${cls}">${line.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;

  } catch(e) {
    document.getElementById('status-txt').textContent = 'Помилка: ' + e;
  }
}

setInterval(() => {
  secs--;
  document.getElementById('timer').textContent = `оновлення через ${secs}с`;
  if (secs <= 0) { secs = 10; refresh(); }
}, 1000);


async function resetBot() {
  if (!confirm(`Скинути всі угоди і повернути баланс до $${INITIAL_BALANCE}?`)) return;
  try {
    const r = await fetch('/api/reset', { method: 'POST' });
    const d = await r.json();
    alert(d.message);
    refresh();
  } catch(e) {
    alert('Помилка: ' + e);
  }
}

refresh();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    watchlist_js = str([c.replace("/", "") for c in cfg.WATCHLIST])
    html = HTML.replace("__INITIAL_BALANCE__", str(int(cfg.INITIAL_BALANCE)))
    html = html.replace("__WATCHLIST__", watchlist_js)
    return HTMLResponse(content=html)


if __name__ == "__main__":
    uvicorn.run("dashboard:app", host="0.0.0.0", port=8080, reload=False)
