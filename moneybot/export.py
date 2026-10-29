"""File JSON (liste di righe) per la dashboard, aggiornati a ogni giro."""

import csv
import json
from pathlib import Path

from . import config
from .portfolio import State, iso, read_trades
from .report import benchmark, read_verdicts
from .strategy import Snapshot, is_candidate

EQUITY_FIELDS = ["time", "equity", "benchmark", "cash", "positions"]


def append_equity(state: State, prices: dict[str, float], now: int, path: Path) -> None:
    """Una riga per giro: serve a disegnare l'andamento del capitale."""
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(EQUITY_FIELDS)
        w.writerow([
            iso(now), f"{state.equity(prices):.2f}", f"{benchmark(state, prices):.2f}",
            f"{state.cash:.2f}", len(state.positions),
        ])


def write_all(state: State, snaps: dict[str, Snapshot], p: dict[str, Path], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    prices = {sym: s.close for sym, s in snaps.items()}
    now = max(s.time for s in snaps.values())
    trades = read_trades(p["trades"])
    sells = [t for t in trades if t["side"] == "SELL"]

    summary = [{
        "updated": iso(now),
        "runs": state.runs,
        "mode": "claude" if config.judge_enabled() else "rules",
        "status": "halted" if state.halted else "active",
        "halt_reason": state.halt_reason,
        "start_cash": config.STARTING_CASH,
        "equity": round(state.equity(prices), 2),
        "benchmark": round(benchmark(state, prices), 2),
        "cash": round(state.cash, 2),
        "open_positions": len(state.positions),
        "closed_trades": len(sells),
        "winning_trades": sum(1 for t in sells if float(t["pnl"]) > 0),
        "realized_pnl": round(sum(float(t["pnl"]) for t in sells), 2),
        "fees_paid": round(sum(float(t["fee"]) for t in trades), 2),
    }]

    positions = []
    for sym, pos in state.positions.items():
        price = prices.get(sym, pos.entry_price)
        positions.append({
            "symbol": sym,
            "qty": pos.qty,
            "opened": iso(pos.entry_time),
            "entry_price": pos.entry_price,
            "price": price,
            "invested": round(pos.cost, 2),
            "value": round(pos.qty * price, 2),
            "pnl": round(pos.qty * price - pos.cost, 2),
            "stop": pos.stop,
        })

    trade_rows = [
        {
            "time": t["time"], "symbol": t["symbol"], "side": t["side"],
            "price": float(t["price"]), "value": float(t["value"]),
            "pnl": float(t["pnl"]) if t["side"] == "SELL" else None,
            "reason": t["reason"],
        }
        for t in trades[-200:]
    ]

    market = []
    for sym in config.SYMBOLS:
        s = snaps.get(sym)
        if not s:
            continue
        trend = "up" if s.ema20 > s.ema50 > s.ema200 else ("down" if s.ema20 < s.ema50 < s.ema200 else "sideways")
        market.append({
            "symbol": sym, "price": s.close, "change_24h": round(s.change_24h, 5),
            "change_7d": round(s.change_7d, 5), "rsi": round(s.rsi14, 1), "trend": trend,
            "candidate": is_candidate(s), "held": sym in state.positions,
            "bench_start": state.benchmark_start.get(sym),
        })

    latest = {(v["time"], v["symbol"]): v for v in read_verdicts(p["verdicts"])}
    verdicts = []
    for v in list(latest.values())[-150:]:
        j = v.get("verdict") or {}
        verdicts.append({
            "time": iso(v["time"]), "symbol": v["symbol"], "price": v["price"], "mode": v["mode"],
            "action": j.get("action"), "action_prob": j.get("action_prob"),
            "market_risk": j.get("market_risk"), "reason": j.get("reason"),
            "passed": v["passed"], "bought": v.get("bought", False),
            "outcome_24h": v.get("outcome_24h"), "green": v.get("green"),
        })

    equity = []
    if p["equity"].exists():
        with p["equity"].open(newline="") as f:
            for r in csv.DictReader(f):
                equity.append({
                    "time": r["time"], "equity": float(r["equity"]), "benchmark": float(r["benchmark"]),
                    "cash": float(r["cash"]), "positions": int(r["positions"]),
                })

    for name, rows in [
        ("summary", summary), ("positions", positions), ("trades", trade_rows),
        ("market", market), ("verdicts", verdicts), ("equity", equity[-2000:]),
    ]:
        (out_dir / f"{name}.json").write_text(json.dumps(rows, indent=1))
