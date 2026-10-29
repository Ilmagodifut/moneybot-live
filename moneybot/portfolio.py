"""Portafoglio simulato: soldi finti, prezzi veri, commissioni realistiche."""

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config

TRADE_FIELDS = ["time", "symbol", "side", "price", "qty", "value", "fee", "pnl", "reason"]


@dataclass
class Position:
    qty: float
    entry_price: float
    entry_time: int
    cost: float          # soldi usciti dalla cassa, commissione inclusa
    stop: float
    highest: float
    last_checked: int    # close_time dell'ultima candela già controllata


@dataclass
class State:
    cash: float = config.STARTING_CASH
    positions: dict[str, Position] = field(default_factory=dict)
    cooldowns: dict[str, int] = field(default_factory=dict)
    benchmark_start: dict[str, float] = field(default_factory=dict)
    start_time: int = 0
    peak_equity: float = config.STARTING_CASH
    day: str = ""
    day_start_equity: float = config.STARTING_CASH
    halted: bool = False
    halt_reason: str = ""
    last_run: int = 0
    last_candle: int = 0  # ultima candela già valutata per nuove entrate
    judge_day: str = ""
    judge_calls_today: int = 0
    runs: int = 0

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        raw["positions"] = {k: Position(**v) for k, v in raw.get("positions", {}).items()}
        return cls(**raw)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True))

    def equity(self, prices: dict[str, float]) -> float:
        held = sum(p.qty * prices.get(sym, p.entry_price) for sym, p in self.positions.items())
        return self.cash + held


class Broker:
    """Esegue ordini simulati e li scrive nel registro delle operazioni."""

    def __init__(self, state: State, trades_path: Path):
        self.state = state
        self.trades_path = trades_path

    def buy(self, symbol: str, price: float, atr: float, time_ms: int, equity: float, reason: str,
            fraction: float = config.POSITION_FRACTION) -> bool:
        value = min(equity * fraction, self.state.cash)
        if value < 10:
            return False
        fill = price * (1 + config.SLIPPAGE_RATE)
        fee = value * config.FEE_RATE
        qty = (value - fee) / fill
        self.state.cash -= value
        self.state.positions[symbol] = Position(
            qty=qty,
            entry_price=fill,
            entry_time=time_ms,
            cost=value,
            stop=fill - max(config.STOP_ATR * atr, fill * config.STOP_MIN_PCT),
            highest=fill,
            last_checked=time_ms,
        )
        self._log(time_ms, symbol, "BUY", fill, qty, value, fee, 0.0, reason)
        return True

    def sell(self, symbol: str, price: float, time_ms: int, reason: str) -> float:
        pos = self.state.positions.pop(symbol)
        fill = price * (1 - config.SLIPPAGE_RATE)
        gross = pos.qty * fill
        fee = gross * config.FEE_RATE
        proceeds = gross - fee
        pnl = proceeds - pos.cost
        self.state.cash += proceeds
        self.state.cooldowns[symbol] = time_ms + config.COOLDOWN_HOURS * 3600 * 1000
        self._log(time_ms, symbol, "SELL", fill, pos.qty, proceeds, fee, pnl, reason)
        return pnl

    def _log(self, time_ms, symbol, side, price, qty, value, fee, pnl, reason) -> None:
        self.trades_path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.trades_path.exists()
        with self.trades_path.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(TRADE_FIELDS)
            w.writerow([
                iso(time_ms), symbol, side, f"{price:.8g}", f"{qty:.8g}",
                f"{value:.2f}", f"{fee:.4f}", f"{pnl:.2f}", reason,
            ])


def read_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def iso(time_ms: int) -> str:
    return datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
