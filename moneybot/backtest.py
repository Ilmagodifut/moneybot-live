"""Backtest: le stesse regole del bot, fatte girare su anni di prezzi orari reali.

Claude non è incluso (chiedergli migliaia di giudizi sul passato costerebbe troppo e
lui conosce già com'è andata): qui si misura la base su cui Claude lavora.

Per non ingannarsi, le varianti si scelgono sul primo periodo ("allenamento")
e si giudicano sul secondo ("verifica"), che nessuna scelta ha visto.
"""

import json
import logging
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import config
from .data import BASE_URL
from .indicators import atr, ema, rsi

log = logging.getLogger(__name__)

HOUR_MS = 3600 * 1000
CACHE_DIR = config.ROOT / ".cache" / "klines"


@dataclass(frozen=True)
class Params:
    name: str
    description: str
    stop_atr: float = config.STOP_ATR
    stop_min_pct: float = config.STOP_MIN_PCT
    trail_atr: float = config.TRAIL_ATR
    trail_min_pct: float = config.TRAIL_MIN_PCT
    max_positions: int = config.MAX_OPEN_POSITIONS
    position_fraction: float = config.POSITION_FRACTION
    btc_filter: bool = False       # compra solo se BTC è in trend rialzista
    trend_exit: bool = True        # esce quando la media veloce scende sotto quella media
    fast: int = config.TREND_FAST  # medie mobili (in ore) che definiscono il trend
    mid: int = config.TREND_MID
    slow: int = config.TREND_SLOW
    btc_slow: int = 1200           # media lenta di BTC per il filtro di mercato (50 giorni)
    exit_gap: float = config.TREND_EXIT_GAP  # uscita per trend: media veloce almeno così sotto quella media


# Le regole con cui il bot gira adesso (vedi config.py).
LIVE = Params("lento (attuale)", "Trend su giorni e settimane (medie a 80, 200 e 800 ore), stop larghi. Sono le regole del bot oggi.")
# Le prime regole del bot, su medie orarie: tenute come confronto.
HOURLY = replace(LIVE, name="orarie (prima)", description="Le prime regole del bot: trend su medie a 20, 50 e 200 ore, stop stretti.",
                 fast=20, mid=50, slow=200, stop_atr=2.0, stop_min_pct=0.03, trail_atr=3.0, trail_min_pct=0.04)
VARIANTS = [
    LIVE,
    replace(LIVE, name="lento, uscita al primo incrocio", description="Come le regole attuali, ma esce appena la media a 80 ore tocca quella a 200 (le regole fino al 30/10).",
            exit_gap=0.0),
    replace(LIVE, name="lento + filtro BTC", description="Compra solo se BTC è sopra la sua media di 50 giorni.", btc_filter=True),
    replace(LIVE, name="lento + filtro BTC + solo trailing", description="Filtro BTC; esce solo con il trailing stop.",
            btc_filter=True, trend_exit=False),
    replace(LIVE, name="lento + trailing stretto", description="Trailing al 4% per proteggere prima i guadagni.",
            trail_atr=3.0, trail_min_pct=0.04),
    replace(LIVE, name="lento + 3 posizioni", description="Al massimo 3 posizioni da 15%.", max_positions=3, position_fraction=0.15),
    HOURLY,
    replace(HOURLY, name="orarie + filtro BTC", description="Regole orarie, ma solo quando BTC è in trend rialzista.",
            btc_filter=True, btc_slow=200),
    replace(HOURLY, name="orarie + stop larghi", description="Regole orarie con stop e trailing larghi.",
            stop_atr=3.0, stop_min_pct=0.05, trail_atr=4.0, trail_min_pct=0.06),
]


# ---------------- dati ----------------

def download(symbol: str, days: int) -> list[list]:
    """Candele orarie degli ultimi `days` giorni, salvate in cache su disco."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}.json"
    rows: list[list] = json.loads(path.read_text()) if path.exists() else []
    now_ms = int(time.time() * 1000)
    start = now_ms - days * 24 * HOUR_MS
    rows = [r for r in rows if r[0] >= start]
    cursor = rows[-1][0] + HOUR_MS if rows else start
    while cursor < now_ms - HOUR_MS:
        resp = requests.get(BASE_URL, params={"symbol": symbol, "interval": "1h", "startTime": cursor, "limit": 1000}, timeout=30)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + HOUR_MS
    rows = [r for r in rows if r[6] < now_ms]  # solo candele chiuse
    path.write_text(json.dumps(rows))
    return rows


EMA_PERIODS = (20, 50, 80, 200, 800, 1200)


@dataclass
class Series:
    """Prezzi e indicatori di una moneta, indicizzati per ora di apertura."""
    index: dict[int, int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    emas: dict[int, list[float]]
    rsi14: list[float]
    atr14: list[float]
    volume_ratio: list[float]


def build_series(rows: list[list]) -> Series:
    o = [float(r[1]) for r in rows]
    h = [float(r[2]) for r in rows]
    lo = [float(r[3]) for r in rows]
    c = [float(r[4]) for r in rows]
    v = [float(r[7]) for r in rows]
    # Volume delle ultime 24 ore rispetto alla media giornaliera dei 7 giorni prima (come strategy.snapshot).
    cum = [0.0]
    for x in v:
        cum.append(cum[-1] + x)
    ratio = []
    for i in range(len(v)):
        if i < 24 * 8:
            ratio.append(1.0)
            continue
        last = cum[i + 1] - cum[i + 1 - 24]
        week = (cum[i + 1 - 24] - cum[i + 1 - 24 * 8]) / 7
        ratio.append(last / week if week else 1.0)
    return Series(
        index={int(r[0]): i for i, r in enumerate(rows)},
        open=o, high=h, low=lo, close=c,
        emas={n: ema(c, n) for n in EMA_PERIODS},
        rsi14=rsi(c), atr14=atr(h, lo, c), volume_ratio=ratio,
    )


# ---------------- simulazione ----------------

WARMUP = 1200  # ore di storia prima di poter decidere (serve alla media più lenta)


def passes_rules(s: Series, i: int, p: Params) -> bool:
    f, m, sl = s.emas[p.fast][i], s.emas[p.mid][i], s.emas[p.slow][i]
    dist = (s.close[i] - s.emas[20][i]) / s.atr14[i] if s.atr14[i] else 0.0
    return (
        s.close[i] > sl and f > m > sl and 50 <= s.rsi14[i] <= 70
        and dist < 2.0 and s.volume_ratio[i] >= 0.8
    )


def simulate(series: dict[str, Series], times: list[int], p: Params) -> dict:
    fee, slip = config.FEE_RATE, config.SLIPPAGE_RATE
    cash = config.STARTING_CASH
    positions: dict[str, dict] = {}
    cooldown: dict[str, int] = {}
    trades: list[dict] = []
    curve: list[tuple[int, float]] = []
    day, day_start, peak, max_dd = None, cash, cash, 0.0
    exposure_hours = 0

    def sell(sym: str, price: float, t: int, reason: str) -> None:
        nonlocal cash
        pos = positions.pop(sym)
        gross = pos["qty"] * price * (1 - slip)
        proceeds = gross * (1 - fee)
        cash += proceeds
        cooldown[sym] = t + config.COOLDOWN_HOURS * HOUR_MS
        trades.append({"symbol": sym, "entry": pos["t"], "exit": t, "pnl": proceeds - pos["cost"],
                       "ret": proceeds / pos["cost"] - 1, "reason": reason})

    btc = series.get("BTC")
    for t in times:
        # 1. gestione posizioni sulla candela t
        for sym in list(positions):
            s = series[sym]
            i = s.index.get(t)
            if i is None:
                continue
            pos = positions[sym]
            if s.low[i] <= pos["stop"]:
                sell(sym, min(s.open[i], pos["stop"]), t, "stop")
                continue
            pos["high"] = max(pos["high"], s.close[i])
            trail = max(p.trail_atr * s.atr14[i], pos["high"] * p.trail_min_pct)
            pos["stop"] = max(pos["stop"], pos["high"] - trail)
            if p.trend_exit and s.emas[p.fast][i] < s.emas[p.mid][i] * (1 - p.exit_gap):
                sell(sym, s.close[i], t, "trend")

        # 2. capitale e freni di sicurezza
        held = 0.0
        for sym, pos in positions.items():
            s = series[sym]
            i = s.index.get(t)
            held += pos["qty"] * (s.close[i] if i is not None else pos["price"])
        equity = cash + held
        curve.append((t, equity))
        exposure_hours += bool(positions)
        d = t // (24 * HOUR_MS)
        if d != day:
            day, day_start = d, equity
        peak = max(peak, equity)
        max_dd = max(max_dd, 1 - equity / peak)
        if equity <= day_start * (1 - config.DAILY_LOSS_LIMIT):
            continue

        # 3. nuove entrate
        if p.btc_filter:
            bi = btc.index.get(t) if btc else None
            if bi is None or not (btc.emas[p.mid][bi] > btc.emas[p.btc_slow][bi] and btc.close[bi] > btc.emas[p.btc_slow][bi]):
                continue
        candidates = []
        for sym, s in series.items():
            i = s.index.get(t)
            if i is None or i < WARMUP or sym in positions or cooldown.get(sym, 0) > t:
                continue
            if passes_rules(s, i, p):
                candidates.append((s.volume_ratio[i], sym, i))
        for _, sym, i in sorted(candidates, reverse=True):
            if len(positions) >= p.max_positions:
                break
            s = series[sym]
            value = min(equity * p.position_fraction, cash)
            if value < 10:
                break
            fill = s.close[i] * (1 + slip)
            cash -= value
            positions[sym] = {
                "qty": value * (1 - fee) / fill, "cost": value, "price": fill, "t": t, "high": fill,
                "stop": fill - max(p.stop_atr * s.atr14[i], fill * p.stop_min_pct),
            }

    return {"curve": curve, "trades": trades, "max_dd": max_dd, "exposure": exposure_hours / max(1, len(times))}


def stats(result: dict, start: int, end: int) -> dict:
    """Metriche su un intervallo [start, end) della simulazione."""
    curve = [(t, e) for t, e in result["curve"] if start <= t < end]
    trades = [x for x in result["trades"] if start <= x["exit"] < end]
    if not curve:
        return {}
    peak, dd = curve[0][1], 0.0
    for _, e in curve:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak)
    wins = [x for x in trades if x["pnl"] > 0]
    gains = sum(x["pnl"] for x in wins)
    losses = -sum(x["pnl"] for x in trades if x["pnl"] <= 0)
    ret = curve[-1][1] / curve[0][1] - 1
    return {
        "return": round(ret, 4),
        "max_drawdown": round(dd, 4),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 3) if trades else None,
        "profit_factor": round(gains / losses, 2) if losses else None,
        "avg_trade": round(sum(x["ret"] for x in trades) / len(trades), 4) if trades else None,
        "score": round(ret / dd, 2) if dd else None,  # rendimento per unità di calo massimo
    }


def buy_and_hold(series: dict[str, Series], start: int, end: int, symbols: list[str]) -> float:
    ratios = []
    for sym in symbols:
        s = series.get(sym)
        if not s:
            continue
        ts = [t for t in s.index if start <= t < end]
        if ts:
            ratios.append(s.close[s.index[max(ts)]] / s.close[s.index[min(ts)]])
    return round(sum(ratios) / len(ratios) - 1, 4) if ratios else 0.0


def run(days: int = 730, out_dir: Path | None = None) -> dict:
    series = {}
    for sym in config.SYMBOLS:
        try:
            rows = download(sym + config.QUOTE, days)
        except requests.RequestException as e:
            log.warning("storico di %s non disponibile: %s", sym, e)
            continue
        if len(rows) > WARMUP + 24:
            series[sym] = build_series(rows)
    times = sorted(series["BTC"].index)
    times = times[WARMUP:]
    split = times[int(len(times) * 0.6)]
    end = times[-1] + HOUR_MS
    everyone = list(series)

    results = []
    curves = {}
    for p in VARIANTS:
        res = simulate(series, times, p)
        results.append({
            "name": p.name, "family": "regole del bot", "description": p.description, "params": asdict(p),
            "train": stats(res, times[0], split),
            "test": stats(res, split, end),
            "full": stats(res, times[0], end),
        })
        curves[p.name] = res["curve"]
        log.info("%s: allenamento %+.1f%%, verifica %+.1f%%", p.name,
                 100 * results[-1]["train"]["return"], 100 * results[-1]["test"]["return"])

    from .strategies import STRATEGIES, simulate_weights
    for strat in STRATEGIES:
        res = simulate_weights(series, times, strat)
        results.append({
            "name": strat.name, "family": strat.family, "description": strat.description,
            "train": stats(res, times[0], split),
            "test": stats(res, split, end),
            "full": stats(res, times[0], end),
        })
        curves[strat.name] = res["curve"]
        log.info("%s: allenamento %+.1f%%, verifica %+.1f%%", strat.name,
                 100 * results[-1]["train"]["return"], 100 * results[-1]["test"]["return"])

    # La scelta usa SOLO il periodo di allenamento: vince il rendimento più alto.
    best = max(results, key=lambda r: r["train"]["return"])

    iso = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    hold_curve = []
    for t, _ in curves[LIVE.name][::24]:
        ratios = [s.close[s.index[t]] / s.close[s.index[times[0]]] for s in series.values()
                  if t in s.index and times[0] in s.index]
        if ratios:
            hold_curve.append(config.STARTING_CASH * sum(ratios) / len(ratios))
    curve_rows = []
    live_daily = curves[LIVE.name][::24]
    hourly_daily = curves[HOURLY.name][::24]
    for k, (t, e) in enumerate(live_daily):
        curve_rows.append({
            "time": iso(t), "attuale": round(e, 2), "orarie": round(hourly_daily[k][1], 2),
            "compra_e_tieni": round(hold_curve[k], 2) if k < len(hold_curve) else None,
        })

    report = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "start": iso(times[0]), "split": iso(split), "end": iso(times[-1]),
        "symbols": everyone,
        "buy_and_hold": {
            "train": buy_and_hold(series, times[0], split, everyone),
            "test": buy_and_hold(series, split, end, everyone),
            "full": buy_and_hold(series, times[0], end, everyone),
            "btc_test": buy_and_hold(series, split, end, ["BTC"]),
        },
        "variants": results,
        "best": best["name"],
        "live": LIVE.name,
        "curve": curve_rows,
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "backtest.json").write_text(json.dumps(report, indent=1))
    return report
