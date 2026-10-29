"""I capitali "rapidi": 1.000 $ finti ciascuno, strategia di breve periodo.

Comprano una moneta quando scende di colpo sotto la sua media a 200 ore (circa 8 giorni),
purché il trend di 50 giorni non sia crollato; rivendono quando il prezzo torna alla media,
dopo al massimo 5 giorni o con uno stop del 10%. Solo regole: niente Claude.

- RAPIDO: solo BTC ed ETH, compra al -4%, metà capitale per moneta (il migliore nel backtest).
- RAPIDO_360: tutte le 18 crypto, compra al -8%, fino a 6 posizioni insieme
  (la variante migliore su tutte le monete, ma nel backtest ha reso meno e con cali più forti).
"""

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from . import config
from .data import Candle
from .indicators import ema
from .portfolio import Broker, State, iso, read_trades

log = logging.getLogger(__name__)

MAX_HOURS = 120      # uscita dopo 5 giorni al massimo
STOP = 0.10          # stop del 10% sotto l'entrata
HOUR_MS = 3600 * 1000


@dataclass(frozen=True)
class Setup:
    folder: str          # cartella dello stato in state/
    prefix: str          # prefisso dei file per la web app
    symbols: tuple
    drop: float          # ribasso sotto la media a 200 ore per comprare
    max_positions: int

    @property
    def fraction(self) -> float:
        return 1 / self.max_positions

    @property
    def trend_floor(self) -> float:
        return 2 * self.drop  # non troppo sotto la media di 50 giorni (come nel backtest)


RAPIDO = Setup("rapido", "fast", ("BTC", "ETH"), drop=0.04, max_positions=2)
RAPIDO_360 = Setup("rapido360", "fast360", tuple(config.SYMBOLS), drop=0.08, max_positions=6)

# Compatibilità con il resto del codice e con i test.
SYMBOLS = list(RAPIDO.symbols)
DROP = RAPIDO.drop


def paths(state_dir: Path, setup: Setup = RAPIDO) -> dict[str, Path]:
    d = state_dir / setup.folder
    return {"dir": d, "state": d / "state.json", "trades": d / "trades.csv", "equity": d / "equity.csv"}


def levels(candles: list[Candle]) -> tuple[float, float, float]:
    closes = [c.close for c in candles]
    return closes[-1], ema(closes, 200)[-1], ema(closes, 1200)[-1]


def run(candles: dict[str, list[Candle]], now: int, new_candle: bool, state_dir: Path, out_dir: Path,
        setup: Setup = RAPIDO) -> None:
    p = paths(state_dir, setup)
    p["dir"].mkdir(parents=True, exist_ok=True)
    state = State.load(p["state"])
    broker = Broker(state, p["trades"])
    prices, lv = {}, {}

    for sym in setup.symbols:
        c = candles.get(sym)
        if c and len(c) >= 1200:
            lv[sym] = levels(c)
            prices[sym] = lv[sym][0]

    # Uscite.
    for sym in list(state.positions):
        if sym not in lv:
            continue
        price, mean, _ = lv[sym]
        pos = state.positions[sym]
        held_hours = (now - pos.entry_time) // HOUR_MS
        if price >= mean:
            broker.sell(sym, price, now, "rimbalzo: tornato alla media")
        elif price <= pos.stop:
            broker.sell(sym, price, now, "stop -10%")
        elif held_hours >= MAX_HOURS:
            broker.sell(sym, price, now, "tempo scaduto (5 giorni)")

    # Entrate: prima i crolli più profondi, fino al numero massimo di posizioni.
    if new_candle:
        candidates = sorted(
            (price / mean, sym) for sym, (price, mean, slow) in lv.items()
            if sym not in state.positions and price < mean * (1 - setup.drop) and price > slow * (1 - setup.trend_floor)
        )
        for _, sym in candidates:
            if len(state.positions) >= setup.max_positions:
                break
            price, mean, _ = lv[sym]
            equity = state.equity(prices)
            if broker.buy(sym, price, 0.0, now, equity, f"ribasso {price / mean - 1:+.1%} sotto la media", fraction=setup.fraction):
                state.positions[sym].stop = state.positions[sym].entry_price * (1 - STOP)
                log.info("%s: compra %s", setup.folder, sym)

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
    watch = sorted(
        ({"symbol": sym, "price": price, "mean": round(mean, 6), "distance": round(price / mean - 1, 5), "trigger": -setup.drop}
         for sym, (price, mean, _) in lv.items()),
        key=lambda x: x["distance"],
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{setup.prefix}_watch.json").write_text(json.dumps(watch, indent=1))
    export(state, prices, now, p, out_dir, setup.prefix)


def export(state: State, prices: dict[str, float], now: int, p: dict[str, Path], out_dir: Path, prefix: str = "fast") -> None:
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
    for name, rows in [("summary", summary), ("positions", positions), ("trades", trade_rows), ("equity", equity[-2000:])]:
        (out_dir / f"{prefix}_{name}.json").write_text(json.dumps(rows, indent=1))
