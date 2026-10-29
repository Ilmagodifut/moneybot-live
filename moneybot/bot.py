"""Un giro del bot: controlla le posizioni, cerca nuove entrate, salva tutto.

Va lanciato una volta all'ora (lo fa GitHub Actions, vedi README).
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from . import config, export, fast, judge, report
from .data import Candle, fetch_candles
from .portfolio import Broker, State
from .strategy import Snapshot, is_candidate, rules_only_gate, snapshot, trend_broken

log = logging.getLogger(__name__)

HOUR_MS = 3600 * 1000


def paths(state_dir: Path) -> dict[str, Path]:
    return {
        "state": state_dir / "state.json",
        "trades": state_dir / "trades.csv",
        "verdicts": state_dir / "verdicts.jsonl",
        "equity": state_dir / "equity.csv",
        "dashboard": state_dir / "dashboard",
    }


def run() -> None:
    p = paths(config.STATE_DIR)
    state = State.load(p["state"])
    broker = Broker(state, p["trades"])

    candles: dict[str, list[Candle]] = {}
    for sym in config.SYMBOLS:
        try:
            candles[sym] = fetch_candles(sym + config.QUOTE, config.INTERVAL, config.CANDLE_LIMIT)
        except RuntimeError as e:
            log.warning("%s", e)
    snaps = {sym: s for sym, c in candles.items() if (s := snapshot(sym, c))}
    if not snaps:
        raise SystemExit("nessun dato di mercato disponibile: riprovo al prossimo giro")
    prices = {sym: s.close for sym, s in snaps.items()}
    now = max(s.time for s in snaps.values())

    if not state.benchmark_start:
        state.benchmark_start = dict(prices)
        state.start_time = now

    manage_positions(state, broker, candles, snaps)

    equity = state.equity(prices)
    allow_entries = update_safety(state, equity, now)

    fill_outcomes(p["verdicts"], candles)

    # Ogni candela si valuta una volta sola, anche se il bot gira più volte nella stessa ora.
    new_candle = now > state.last_candle
    if allow_entries and new_candle:
        look_for_entries(state, broker, snaps, equity, now, p["verdicts"])

    if new_candle:
        export.append_equity(state, prices, now, p["equity"])
        state.last_candle = now
    state.last_run = int(time.time() * 1000)
    state.runs += 1
    state.save(p["state"])
    export.write_all(state, snaps, p, p["dashboard"])
    report.write(state, snaps, p, config.REPORT_PATH)

    # Il secondo capitale, separato: strategia rapida su BTC ed ETH.
    for setup in (fast.RAPIDO, fast.RAPIDO_360):
        try:
            fast.run(candles, now, new_candle, config.STATE_DIR, p["dashboard"], setup)
        except Exception:
            log.exception("capitale %s: errore, gli altri capitali non sono toccati", setup.folder)

    # Il terzo capitale: 200 prodotti tra azioni, ETF e crypto, ribilanciato ogni mese.
    try:
        from . import multi
        multi.run_live(config.STATE_DIR, p["dashboard"])
    except Exception:
        log.exception("capitale multi-mercato: errore, gli altri capitali non sono toccati")
    log.info("giro completato: capitale %.2f$, posizioni aperte %d", state.equity(prices), len(state.positions))


def manage_positions(state: State, broker: Broker, candles: dict[str, list[Candle]], snaps: dict[str, Snapshot]) -> None:
    for sym in list(state.positions):
        if sym not in snaps:
            continue
        pos = state.positions[sym]
        s = snaps[sym]
        closed = False
        for c in candles[sym]:
            if c.close_time <= pos.last_checked:
                continue
            if c.low <= pos.stop:
                # Se il prezzo apre già sotto lo stop, si esce al prezzo di apertura.
                broker.sell(sym, min(c.open, pos.stop), c.close_time, "stop")
                closed = True
                break
            pos.highest = max(pos.highest, c.close)
            trail = max(config.TRAIL_ATR * s.atr14, pos.highest * config.TRAIL_MIN_PCT)
            pos.stop = max(pos.stop, pos.highest - trail)
            pos.last_checked = c.close_time
        if not closed and trend_broken(s):
            broker.sell(sym, s.close, s.time, f"trend rotto (media {config.TREND_FAST}h sotto {config.TREND_MID}h)")


def update_safety(state: State, equity: float, now: int) -> bool:
    today = datetime.fromtimestamp(now / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    if state.day != today:
        state.day = today
        state.day_start_equity = equity
    state.peak_equity = max(state.peak_equity, equity)

    if equity <= state.peak_equity * (1 - config.MAX_DRAWDOWN):
        state.halted = True
        state.halt_reason = f"perdita oltre il {config.MAX_DRAWDOWN:.0%} dal massimo"
    if state.halted:
        log.warning("bot fermo: %s (riattivalo a mano, vedi README)", state.halt_reason)
        return False
    if equity <= state.day_start_equity * (1 - config.DAILY_LOSS_LIMIT):
        log.warning("limite di perdita giornaliera raggiunto: niente acquisti fino a domani")
        return False
    return True


def look_for_entries(state: State, broker: Broker, snaps: dict[str, Snapshot], equity: float, now: int, verdicts_path: Path) -> None:
    use_claude = config.judge_enabled()
    client = anthropic.Anthropic() if use_claude else None
    btc = snaps.get("BTC")

    candidates = [
        s for sym, s in snaps.items()
        if sym not in state.positions
        and state.cooldowns.get(sym, 0) <= now
        and is_candidate(s)
    ]
    # Prima le monete con più interesse (volume in crescita).
    candidates.sort(key=lambda s: s.volume_ratio, reverse=True)

    if state.judge_day != state.day:
        state.judge_day = state.day
        state.judge_calls_today = 0

    calls = 0
    for s in candidates:
        if len(state.positions) >= config.MAX_OPEN_POSITIONS:
            break
        # Cosa farebbero le sole regole: serve a capire se Claude aggiunge valore.
        record = {"time": s.time, "symbol": s.symbol, "price": s.close, "snapshot": s.to_dict(), "rules_passed": rules_only_gate(s)}
        if use_claude:
            if calls >= config.MAX_JUDGE_CALLS_PER_RUN or state.judge_calls_today >= config.MAX_JUDGE_CALLS_PER_DAY:
                break
            calls += 1
            state.judge_calls_today += 1
            try:
                verdict = judge.ask(client, s, btc)
            except judge.JudgeUnavailable:
                # Senza Claude il bot non si blocca: da qui in poi decidono le regole.
                use_claude = False
        if use_claude:
            record["mode"] = "claude"
            record["verdict"] = verdict.__dict__ if verdict else None
            passed = bool(verdict and verdict.passes_gate())
            reason = f"Claude: {verdict.action} {verdict.action_prob:.2f}" if verdict else "Claude: nessuna risposta"
        else:
            record["mode"] = "rules"
            passed = rules_only_gate(s)
            reason = "regole"
        record["passed"] = passed
        record["bought"] = passed and broker.buy(s.symbol, s.close, s.atr14, s.time, equity, reason)
        append_jsonl(verdicts_path, record)
        log.info("%s: %s -> %s", s.symbol, reason, "COMPRA" if record["bought"] else "passa")


def fill_outcomes(verdicts_path: Path, candles: dict[str, list[Candle]]) -> None:
    """Segna com'è andata ogni verdetto 24 ore dopo: serve al controllo di calibrazione."""
    if not verdicts_path.exists():
        return
    rows = [json.loads(line) for line in verdicts_path.read_text().splitlines() if line.strip()]
    changed = False
    for r in rows:
        if "outcome_24h" in r or r["symbol"] not in candles:
            continue
        target = r["time"] + 24 * HOUR_MS
        later = [c for c in candles[r["symbol"]] if c.close_time >= target]
        if not later:
            continue
        change = (later[0].close - r["price"]) / r["price"]
        r["outcome_24h"] = change
        # "Verde" solo se il guadagno copre commissioni e slippage di andata e ritorno.
        r["green"] = change > 2 * (config.FEE_RATE + config.SLIPPAGE_RATE)
        changed = True
    if changed:
        verdicts_path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")
