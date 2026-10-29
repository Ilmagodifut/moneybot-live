"""Il capitale rapido: 1.000 $ finti separati, strategia di breve periodo.

Compra BTC o ETH quando scendono di colpo del 6% sotto la loro media a 200 ore
(circa 8 giorni), purché il trend di 50 giorni non sia crollato; rivende quando il
prezzo torna alla media, dopo al massimo 5 giorni o con uno stop del 10%.
Metà del capitale per moneta. Solo regole: niente Claude, per non spendere credito.
È la stessa strategia "compra i ribassi BTC/ETH" del backtest.
"""

import csv
import json
import logging
from pathlib import Path

from . import config
from .data import Candle
from .indicators import ema
from .portfolio import Broker, State, iso, read_trades

log = logging.getLogger(__name__)

SYMBOLS = ["BTC", "ETH"]
FRACTION = 0.5       # metà del capitale per moneta
DROP = 0.06          # ribasso sotto la media a 200 ore per comprare
TREND_FLOOR = 0.12   # ...ma non più del 12% sotto la media di 50 giorni
MAX_HOURS = 120      # uscita dopo 5 giorni al massimo
STOP = 0.10          # stop del 10% sotto l'entrata
HOUR_MS = 3600 * 1000


def paths(state_dir: Path) -> dict[str, Path]:
    d = state_dir / "rapido"
    return {"dir": d, "state": d / "state.json", "trades": d / "trades.csv", "equity": d / "equity.csv"}


def levels(candles: list[Candle]) -> tuple[float, float, float]:
    closes = [c.close for c in candles]
    return closes[-1], ema(closes, 200)[-1], ema(closes, 1200)[-1]


def run(candles: dict[str, list[Candle]], now: int, new_candle: bool, state_dir: Path, out_dir: Path) -> None:
    p = paths(state_dir)
    p["dir"].mkdir(parents=True, exist_ok=True)
    state = State.load(p["state"])
    broker = Broker(state, p["trades"])
    prices = {}

    for sym in SYMBOLS:
        c = candles.get(sym)
        if not c or len(c) < 1200:
            continue
        price, mean, slow = levels(c)
        prices[sym] = price
        pos = state.positions.get(sym)
        if pos:
            held_hours = (now - pos.entry_time) // HOUR_MS
            if price >= mean:
                broker.sell(sym, price, now, "rimbalzo: tornato alla media")
            elif price <= pos.stop:
                broker.sell(sym, price, now, "stop -10%")
            elif held_hours >= MAX_HOURS:
                broker.sell(sym, price, now, "tempo scaduto (5 giorni)")
        elif new_candle and price < mean * (1 - DROP) and price > slow * (1 - TREND_FLOOR):
            equity = state.equity(prices)
            if broker.buy(sym, price, 0.0, now, equity, f"ribasso {price / mean - 1:+.1%} sotto la media", fraction=FRACTION):
                state.positions[sym].stop = state.positions[sym].entry_price * (1 - STOP)
            log.info("rapido: compra %s", sym)

    if not state.benchmark_start:
        state.benchmark_start = dict(prices)
        state.start_time = now
    equity = state.equity({**{s: p.entry_price for s, p in state.positions.items()}, **prices})
    if new_candle and now > state.last_candle:
        new = not p["equity"].exists()
        with p["equity"].open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "equity", "cash", "positions"])
            w.writerow([iso(now), f"{equity:.2f}", f"{state.cash:.2f}", len(state.positions)])
        state.last_candle = now
    state.runs += 1
    state.save(p["state"])
    watch = []
    for sym in SYMBOLS:
        c = candles.get(sym)
        if c and len(c) >= 1200:
            price, mean, _ = levels(c)
            watch.append({"symbol": sym, "price": price, "mean": round(mean, 6), "distance": round(price / mean - 1, 5), "trigger": -DROP})
    (out_dir / "fast_watch.json").write_text(json.dumps(watch, indent=1)) if out_dir.exists() else None
    export(state, prices, now, p, out_dir)


def export(state: State, prices: dict[str, float], now: int, p: dict[str, Path], out_dir: Path) -> None:
    trades = read_trades(p["trades"])
    sells = [t for t in trades if t["side"] == "SELL"]
    price_of = lambda sym, pos: prices.get(sym, pos.entry_price)
    summary = [{
        "updated": iso(now),
        "start_cash": config.STARTING_CASH,
        "equity": round(state.cash + sum(pos.qty * price_of(s, pos) for s, pos in state.positions.items()), 2),
        "cash": round(state.cash, 2),
        "open_positions": len(state.positions),
        "closed_trades": len(sells),
        "winning_trades": sum(1 for t in sells if float(t["pnl"]) > 0),
        "realized_pnl": round(sum(float(t["pnl"]) for t in sells), 2),
        "fees_paid": round(sum(float(t["fee"]) for t in trades), 2),
    }]
    positions = [{
        "symbol": s, "qty": pos.qty, "opened": iso(pos.entry_time), "entry_price": pos.entry_price,
        "price": price_of(s, pos), "invested": round(pos.cost, 2), "stop": pos.stop,
    } for s, pos in state.positions.items()]
    trade_rows = [{
        "time": t["time"], "symbol": t["symbol"], "side": t["side"], "price": float(t["price"]),
        "value": float(t["value"]), "pnl": float(t["pnl"]) if t["side"] == "SELL" else None, "reason": t["reason"],
    } for t in trades[-200:]]
    equity = []
    if p["equity"].exists():
        with p["equity"].open(newline="") as f:
            equity = [{"time": r["time"], "equity": float(r["equity"])} for r in csv.DictReader(f)]
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in [("fast_summary", summary), ("fast_positions", positions), ("fast_trades", trade_rows), ("fast_equity", equity[-2000:])]:
        (out_dir / f"{name}.json").write_text(json.dumps(rows, indent=1))
