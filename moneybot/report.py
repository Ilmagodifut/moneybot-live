"""Il report leggibile (REPORT.md) aggiornato a ogni giro."""

import json
from pathlib import Path

from . import config
from .portfolio import State, iso, read_trades
from .strategy import Snapshot, is_candidate

BUCKETS = [(0.0, 0.65), (0.65, 0.75), (0.75, 0.85), (0.85, 1.01)]


def write(state: State, snaps: dict[str, Snapshot], p: dict[str, Path], out: Path) -> None:
    out.write_text(render(state, snaps, read_trades(p["trades"]), read_verdicts(p["verdicts"])))


def read_verdicts(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def render(state: State, snaps: dict[str, Snapshot], trades: list[dict], verdicts: list[dict]) -> str:
    prices = {sym: s.close for sym, s in snaps.items()}
    equity = state.equity(prices)
    now = max((s.time for s in snaps.values()), default=state.last_run)
    mode = f"Claude come giudice (`{config.JUDGE_MODEL}`)" if config.judge_enabled() else "solo regole (Claude non configurato)"
    status = f"FERMO: {state.halt_reason}" if state.halted else "attivo"

    lines = [
        "# MONEYBOT – report (paper trading, soldi finti)",
        "",
        f"Aggiornato: {iso(now)} UTC · giri eseguiti: {state.runs} · modalità: {mode} · stato: **{status}**",
        "",
        "## Capitale",
        "",
        "| | Valore |",
        "|---|---|",
        f"| Capitale iniziale | {config.STARTING_CASH:,.2f} $ |",
        f"| Capitale attuale | **{equity:,.2f} $** ({pct(equity / config.STARTING_CASH - 1)}) |",
        f"| Se avessi comprato e tenuto tutte le monete | {benchmark(state, prices):,.2f} $ |",
        f"| Liquidità | {state.cash:,.2f} $ |",
        f"| Calo massimo dal picco | {pct(equity / state.peak_equity - 1)} |",
        "",
    ]

    lines += ["## Posizioni aperte", ""]
    if state.positions:
        lines += ["| Moneta | Entrata | Prezzo ora | Risultato | Stop |", "|---|---|---|---|---|"]
        for sym, pos in state.positions.items():
            now_price = prices.get(sym, pos.entry_price)
            value = pos.qty * now_price
            lines.append(
                f"| {sym} | {pos.entry_price:.6g} ({iso(pos.entry_time)}) | {now_price:.6g} "
                f"| {pct(value / pos.cost - 1)} | {pos.stop:.6g} |"
            )
    else:
        lines.append("Nessuna.")
    lines.append("")

    sells = [t for t in trades if t["side"] == "SELL"]
    wins = [t for t in sells if float(t["pnl"]) > 0]
    fees = sum(float(t["fee"]) for t in trades)
    lines += [
        "## Operazioni chiuse",
        "",
        f"Chiuse: {len(sells)} · in guadagno: {len(wins)}"
        + (f" ({len(wins) / len(sells):.0%})" if sells else "")
        + f" · risultato totale: {sum(float(t['pnl']) for t in sells):+.2f} $ · commissioni pagate: {fees:.2f} $",
        "",
    ]
    if trades:
        lines += ["Ultime 10 operazioni:", "", "| Ora (UTC) | Moneta | Tipo | Prezzo | Valore | Risultato | Motivo |", "|---|---|---|---|---|---|---|"]
        for t in trades[-10:][::-1]:
            res = f"{float(t['pnl']):+.2f} $" if t["side"] == "SELL" else ""
            lines.append(f"| {t['time']} | {t['symbol']} | {t['side']} | {t['price']} | {t['value']} $ | {res} | {t['reason']} |")
        lines.append("")

    lines += render_judge(verdicts)

    lines += [
        "## Monete osservate",
        "",
        "| Moneta | Prezzo | 24h | 7g | RSI | Trend | Candidata |",
        "|---|---|---|---|---|---|---|",
    ]
    for sym in config.SYMBOLS:
        s = snaps.get(sym)
        if not s:
            lines.append(f"| {sym} | dati mancanti | | | | | |")
            continue
        trend = "su" if s.ema20 > s.ema50 > s.ema200 else ("giù" if s.ema20 < s.ema50 < s.ema200 else "laterale")
        lines.append(
            f"| {sym} | {s.close:.6g} | {pct(s.change_24h)} | {pct(s.change_7d)} | {s.rsi14:.0f} "
            f"| {trend} | {'sì' if is_candidate(s) else ''} |"
        )
    lines += [
        "",
        "---",
        "Questo è un esperimento con soldi finti. Nessun risultato passato garantisce risultati futuri.",
        "",
    ]
    return "\n".join(lines)


def render_judge(verdicts: list[dict]) -> list[str]:
    lines = ["## Verdetti", ""]
    if not verdicts:
        return lines + ["Ancora nessun verdetto.", ""]
    passed = [v for v in verdicts if v["passed"]]
    lines += [f"Valutati: {len(verdicts)} · superato il cancello: {len(passed)} · comprati: {sum(1 for v in verdicts if v.get('bought'))}", ""]

    # Controllo di calibrazione: quando Claude dice "buy" con probabilità X,
    # il prezzo 24 ore dopo è salito circa X volte su 100?
    judged = [v for v in verdicts if v.get("verdict") and "outcome_24h" in v]
    if judged:
        lines += [
            "### Calibrazione di Claude (prezzo 24 ore dopo)",
            "",
            "| Risposta | Probabilità | Casi | Saliti | Variazione media |",
            "|---|---|---|---|---|",
        ]
        for action in ("buy", "watch", "pass"):
            for lo, hi in BUCKETS:
                group = [
                    v for v in judged
                    if v["verdict"]["action"] == action and lo <= v["verdict"]["action_prob"] < hi
                ]
                if not group:
                    continue
                greens = sum(1 for v in group if v["green"])
                avg = sum(v["outcome_24h"] for v in group) / len(group)
                label = f"{lo:.2f}–{min(hi, 1):.2f}"
                lines.append(f"| {action} | {label} | {len(group)} | {greens / len(group):.0%} | {pct(avg)} |")
        lines += [
            "",
            "Se i \"buy\" con probabilità alta non salgono più spesso degli altri, il giudice non sta aggiungendo valore.",
            "",
        ]
    return lines


def benchmark(state: State, prices: dict[str, float]) -> float:
    ratios = [prices[s] / p0 for s, p0 in state.benchmark_start.items() if s in prices and p0]
    if not ratios:
        return config.STARTING_CASH
    return config.STARTING_CASH * sum(ratios) / len(ratios)


def pct(x: float) -> str:
    return f"{x:+.2%}"
