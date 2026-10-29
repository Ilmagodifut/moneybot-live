"""Il capitale "Rapido 200": compra i cali bruschi su tutti i 200 prodotti.

Una volta al giorno, dopo la chiusura di Wall Street, guarda i 200 prodotti (azioni,
ETF e crypto, prezzi giornalieri da Yahoo Finance):
- compra quelli in trend di fondo positivo (sopra la media di 200 giorni) che negli
  ultimi 2 giorni sono scesi bruscamente (RSI a 2 giorni sotto 5), i più "ipervenduti"
  per primi, fino a 10 posizioni da un decimo del capitale;
- rivende quando il prezzo torna sopra la media degli ultimi 5 giorni, o dopo 2 settimane.
Nel backtest su circa 20 anni: circa +2% all'anno nel 2006-2018 e +6% dopo, con più di
un'operazione al giorno. Il valore delle posizioni si aggiorna a ogni giro.
"""

import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .multi import DAY, KIND, UNIVERSE, ema_series, load_universe, matrix, quote

log = logging.getLogger(__name__)

SLOTS = 10
RSI_MAX = 5
MAX_DAYS = 14            # due settimane di calendario (circa 10 giorni di borsa)
DECISION_HOUR_UTC = 21   # dopo la chiusura della borsa americana


def rsi2(values: list) -> list:
    """RSI a 2 giorni: sotto 5 vuol dire due giorni di calo molto forte."""
    out, ag, al, prev = [], None, None, None
    for v in values:
        if v is None or prev is None:
            out.append(None)
            prev = v
            continue
        change, prev = v - prev, v
        gain, loss = max(change, 0.0), max(-change, 0.0)
        ag = gain if ag is None else (ag + gain) / 2
        al = loss if al is None else (al + loss) / 2
        out.append(100.0 if al == 0 else 100 - 100 / (1 + ag / al))
    return out


def sma5(values: list, i: int) -> float | None:
    window = values[i - 4:i + 1] if i >= 4 else []
    return sum(window) / 5 if len(window) == 5 and None not in window else None


def signals(series: dict[str, list], i: int) -> tuple[list[tuple[float, str]], dict[str, float]]:
    """Prodotti in trend di fondo ordinati per RSI a 2 giorni (i primi con RSI sotto RSI_MAX
    sono da comprare) e media a 5 giorni di ogni prodotto, al giorno i."""
    candidates, mean5 = [], {}
    for sym, vals in series.items():
        price = vals[i]
        if price is None:
            continue
        m = sma5(vals, i)
        if m is not None:
            mean5[sym] = m
        if vals[max(0, i - 252)] is None:   # serve un anno di storia
            continue
        e200 = ema_series(vals, 200)[i]
        r = rsi2(vals)[i]
        if e200 is not None and r is not None and price > e200:
            candidates.append((r, sym))
    return sorted(candidates), mean5


def paths(state_dir: Path) -> dict[str, Path]:
    d = state_dir / "rapido200"
    return {"dir": d, "state": d / "state.json", "trades": d / "trades.csv", "equity": d / "equity.csv", "meta": d / "meta.json"}


def run_live(state_dir: Path, out_dir: Path, now_s: int | None = None) -> None:
    from .portfolio import Broker, State, iso, read_trades

    now_s = now_s or int(time.time())
    p = paths(state_dir)
    p["dir"].mkdir(parents=True, exist_ok=True)
    state = State.load(p["state"])
    broker = Broker(state, p["trades"])
    meta = json.loads(p["meta"].read_text()) if p["meta"].exists() else {"last_day": "", "watch": []}

    prices = {}
    for sym in state.positions:
        q = quote(sym)
        if q:
            prices[sym] = q

    now = datetime.fromtimestamp(now_s, tz=timezone.utc)
    today = now.strftime("%Y-%m-%d")
    due = now.hour >= DECISION_HOUR_UTC and now.weekday() < 5 and meta.get("last_day") != today
    if due:
        data = load_universe(UNIVERSE, fresh=True)
        days, series = matrix(data)
        i = len(days) - 1
        candidates, mean5 = signals(series, i)
        last = {s: series[s][i] for s in series if series[s][i]}
        t_ms = now_s * 1000
        for sym in list(state.positions):
            price = last.get(sym) or prices.get(sym)
            if price is None:
                continue
            pos = state.positions[sym]
            if sym in mean5 and price > mean5[sym]:
                broker.sell(sym, price, t_ms, "rimbalzo: sopra la media di 5 giorni")
            elif now_s * 1000 - pos.entry_time >= MAX_DAYS * DAY * 1000:
                broker.sell(sym, price, t_ms, "tempo scaduto (2 settimane)")
        prices.update({s: last[s] for s in state.positions if s in last})
        for r, sym in candidates:
            if r >= RSI_MAX or len(state.positions) >= SLOTS:
                break
            if sym in state.positions or sym not in last:
                continue
            equity = state.equity({**prices, **{s: last[s] for s in last if s in state.positions}})
            if broker.buy(sym, last[sym], 0.0, t_ms, equity, f"calo brusco (RSI 2 giorni {r:.1f})", fraction=1 / SLOTS):
                prices[sym] = last[sym]
        meta = {"last_day": today, "watch": [{"symbol": s, "kind": KIND.get(s, ""), "rsi": round(r, 1)} for r, s in candidates[:8]]}
        candidates = [c for c in candidates if c[0] < RSI_MAX]
        p["meta"].write_text(json.dumps(meta))
        log.info("rapido 200: %d posizioni, %d candidati oggi", len(state.positions), len(candidates))

    equity = state.equity({**{s: pos.entry_price for s, pos in state.positions.items()}, **prices})
    hour_ms = (now_s - now_s % 3600) * 1000
    if hour_ms > state.last_candle:
        new = not p["equity"].exists()
        with p["equity"].open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "equity"])
            w.writerow([iso(hour_ms), f"{equity:.2f}"])
        state.last_candle = hour_ms
    state.runs += 1
    state.save(p["state"])

    trades = read_trades(p["trades"])
    sells = [t for t in trades if t["side"] == "SELL"]
    summary = [{
        "updated": iso(now_s * 1000), "start_cash": config.STARTING_CASH, "equity": round(equity, 2),
        "cash": round(state.cash, 2), "open_positions": len(state.positions), "closed_trades": len(sells),
        "winning_trades": sum(1 for t in sells if float(t["pnl"]) > 0),
        "fees_paid": round(sum(float(t["fee"]) for t in trades), 2), "last_decision": meta.get("last_day", ""),
    }]
    positions = [{
        "symbol": sym, "kind": KIND.get(sym, ""), "qty": pos.qty, "opened": iso(pos.entry_time),
        "entry_price": pos.entry_price, "price": prices.get(sym, pos.entry_price), "invested": round(pos.cost, 2),
    } for sym, pos in state.positions.items()]
    trade_rows = [{
        "time": t["time"], "symbol": t["symbol"], "side": t["side"], "price": float(t["price"]),
        "value": float(t["value"]), "pnl": float(t["pnl"]) if t["side"] == "SELL" else None, "reason": t["reason"],
    } for t in trades[-200:]]
    with p["equity"].open(newline="") as f:
        eq_rows = [{"time": r["time"], "equity": float(r["equity"])} for r in csv.DictReader(f)]
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in [("summary", summary), ("positions", positions), ("trades", trade_rows),
                       ("equity", eq_rows[-3000:]), ("watch", meta.get("watch", []))]:
        (out_dir / f"rapid200_{name}.json").write_text(json.dumps(rows, indent=1))
