"""Il capitale multi-mercato: 200 prodotti tra azioni USA, ETF di tutto il mondo e crypto.

Ogni settimana tiene, in parti uguali, i prodotti saliti di più negli ultimi mesi,
purché siano sopra la loro media di 200 giorni. Se la borsa americana (SPY) è sotto
la sua media di 200 giorni, compra solo ciò che è ancora in trend (spesso oro,
obbligazioni o niente). Prezzi giornalieri da Yahoo Finance, corretti per dividendi.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
HEADERS = {"User-Agent": "Mozilla/5.0"}
CACHE_DIR = config.ROOT / ".cache" / "daily"
DAY = 86400

STOCKS = """AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B LLY JPM V UNH XOM MA JNJ PG HD COST MRK ABBV
CVX KO PEP ADBE WMT CRM BAC NFLX AMD TMO MCD ORCL CSCO ACN ABT LIN DHR INTU WFC TXN DIS VZ PM AMGN
CMCSA IBM NOW QCOM CAT GE ISRG UNP NEE HON SPGI LOW BKNG RTX GS T PFE ELV AMAT SYK BLK UBER PLD MS
TJX DE AXP MDT LMT VRTX SCHW C ADI MU LRCX ETN CB PANW REGN SBUX BMY GILD KLAC MO SO DUK BA CRWD
ANET SHOP NKE UPS ADP ICE CME PGR ZTS MCK CI EOG SLB FCX MPC WM""".split()
ETFS = """SPY QQQ IWM DIA MDY EFA EEM VGK EWJ EWZ FXI INDA EWG EWU EWQ EWI EWP EWC EWA EWT EWY EWW MCHI
XLK XLF XLV XLE XLI XLY XLP XLU XLB XLRE XLC SMH IBB XBI KRE ITB XHB XME GDX GDXJ TAN ICLN ARKK
TLT IEF SHY LQD HYG TIP AGG EMB BNDX GLD SLV USO UNG DBA DBC CPER PPLT VNQ UUP FXE FXY MTUM QUAL
USMV VLUE SCHD IJR""".split()
CRYPTO = """BTC-USD ETH-USD SOL-USD BNB-USD XRP-USD ADA-USD DOGE-USD AVAX-USD LINK-USD DOT-USD LTC-USD
TRX-USD NEAR-USD ATOM-USD XLM-USD BCH-USD ETC-USD HBAR-USD""".split()
UNIVERSE = STOCKS + ETFS + CRYPTO

KIND = {**{s: "azione" for s in STOCKS}, **{s: "ETF" for s in ETFS}, **{s: "crypto" for s in CRYPTO}}
UNIVERSE_KIND = {"solo ETF": "ETF", "solo azioni": "azione", "solo crypto": "crypto"}


# ---------------- dati ----------------

def download(symbol: str, years: int = 21, fresh: bool = False) -> list[tuple[int, float]]:
    """Chiusure giornaliere corrette (data in secondi, prezzo), con cache su disco."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{symbol}.json"
    if path.exists() and not fresh and time.time() - path.stat().st_mtime < 12 * 3600:
        return [tuple(x) for x in json.loads(path.read_text())]
    now = int(time.time())
    for attempt in range(3):
        try:
            r = requests.get(YAHOO + symbol, headers=HEADERS, timeout=30,
                             params={"period1": now - years * 365 * DAY, "period2": now, "interval": "1d"})
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            break
        except (requests.RequestException, KeyError, TypeError, IndexError) as e:
            err = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"{symbol}: {err}")
    ts = res.get("timestamp") or []
    adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose") or res["indicators"]["quote"][0]["close"]
    rows = [(t - t % DAY, float(c)) for t, c in zip(ts, adj) if c is not None and c > 0]
    path.write_text(json.dumps(rows))
    return rows


def load_universe(symbols: list[str], fresh: bool = False) -> dict[str, dict[int, float]]:
    data = {}
    for sym in symbols:
        try:
            rows = download(sym, fresh=fresh)
        except RuntimeError as e:
            log.warning("niente dati per %s", e)
            continue
        if rows:
            data[sym] = dict(rows)
        time.sleep(0.15)
    return data


# ---------------- strategia ----------------

@dataclass(frozen=True)
class MultiParams:
    name: str
    top: int = 10            # quanti prodotti tenere
    lookback: int = 126      # giorni di borsa per misurare la forza (126 = 6 mesi)
    every: int = 5           # ogni quanti giorni di borsa ribilancia (5 = settimana)
    market_filter: bool = True
    universe: str = "tutti"  # "tutti", "solo ETF", "solo azioni", "solo crypto"


# Scelta dal backtest (migliore sul periodo di allenamento 2006-2018).
LIVE = MultiParams("top 10 su 12 mesi, mensile, senza filtro", top=10, lookback=252, every=21, market_filter=False)
REBALANCE_DAYS = 28      # dal vivo: circa un mese di calendario (≈ 21 giorni di borsa)
DECISION_HOUR_UTC = 21   # decide dopo la chiusura della borsa americana


def grid() -> list[MultiParams]:
    out = []
    for top in (10, 20):
        for lookback in (126, 252):
            for every in (5, 21):
                for mf in (True, False):
                    name = f"top {top} su {lookback // 21} mesi, {'settimanale' if every == 5 else 'mensile'}{'' if mf else ', senza filtro'}"
                    out.append(MultiParams(name, top, lookback, every, mf))
    return out


def matrix(data: dict[str, dict[int, float]]):
    """Allinea tutto sui giorni di borsa di SPY; le crypto usano l'ultimo prezzo disponibile."""
    days = sorted(data["SPY"])
    syms = list(data)
    prices = {s: [] for s in syms}
    last = {s: None for s in syms}
    all_ts = {s: sorted(data[s]) for s in syms}
    ptr = {s: 0 for s in syms}
    for d in days:
        for s in syms:
            ts = all_ts[s]
            while ptr[s] < len(ts) and ts[ptr[s]] <= d:
                last[s] = data[s][ts[ptr[s]]]
                ptr[s] += 1
            prices[s].append(last[s])
    return days, prices


def ema_series(values: list, period: int) -> list:
    k = 2 / (period + 1)
    out, cur = [], None
    for v in values:
        if v is None:
            out.append(None)
            continue
        cur = v if cur is None else v * k + cur * (1 - k)
        out.append(cur)
    return out


def pick(prices, ema200, i: int, p: MultiParams, syms: list[str]) -> list[str]:
    """I prodotti da tenere al giorno i (usa solo dati fino a i)."""
    spy_ok = prices["SPY"][i] > ema200["SPY"][i]
    ranked = []
    for s in syms:
        if p.universe != "tutti" and s != "SPY" and UNIVERSE_KIND[p.universe] != KIND.get(s):
            continue
        if p.universe == "solo azioni" and s == "SPY":
            continue
        now, then = prices[s][i], prices[s][i - p.lookback] if i >= p.lookback else None
        if now is None or then is None or ema200[s][i] is None:
            continue
        # serve un anno di storia prima di fidarsi
        if prices[s][max(0, i - 252)] is None:
            continue
        if now <= ema200[s][i]:
            continue
        if p.market_filter and not spy_ok and KIND.get(s) == "azione":
            continue
        mom = now / then - 1
        if mom > 0:
            ranked.append((mom, s))
    return [s for _, s in sorted(ranked, reverse=True)[:p.top]]


def simulate(days, prices, p: MultiParams) -> dict:
    cost = config.FEE_RATE + config.SLIPPAGE_RATE
    syms = list(prices)
    ema200 = {s: ema_series(prices[s], 200) for s in syms}
    cash, qty = config.STARTING_CASH, {}
    curve, trades, held_log = [], 0, []
    start = 260
    for i in range(start, len(days)):
        equity = cash + sum(q * prices[s][i] for s, q in qty.items())
        if (i - start) % p.every == 0:
            target = pick(prices, ema200, i, p, syms)
            # Come dal vivo: si vende ciò che è uscito dalla lista, si compra ciò che è entrato
            # (1/top del capitale ciascuno); ciò che resta in lista non si tocca.
            for s in list(qty):
                if s not in target:
                    cash += qty.pop(s) * prices[s][i] * (1 - cost)
                    trades += 1
            for s in target:
                if s in qty:
                    continue
                value = min(equity / p.top, cash)
                if value < 10:
                    break
                cash -= value
                qty[s] = value * (1 - cost) / prices[s][i]
                trades += 1
            held_log.append((days[i], target))
            equity = cash + sum(q * prices[s][i] for s, q in qty.items())
        curve.append((days[i], equity))
    return {"curve": curve, "trades": trades, "held": held_log}


def period_stats(curve, a: int, b: int) -> dict:
    seg = [(t, e) for t, e in curve if a <= t < b]
    if len(seg) < 2:
        return {}
    peak, dd = seg[0][1], 0.0
    for _, e in seg:
        peak = max(peak, e)
        dd = max(dd, 1 - e / peak)
    years = (seg[-1][0] - seg[0][0]) / (365.25 * DAY)
    ret = seg[-1][1] / seg[0][1] - 1
    return {"return": round(ret, 4), "cagr": round((1 + ret) ** (1 / years) - 1, 4) if years > 0 else None,
            "max_drawdown": round(dd, 4)}


def run_backtest(out_dir: Path | None = None) -> dict:
    data = load_universe(UNIVERSE)
    days, prices = matrix(data)
    first = days[260]
    split = days[260 + int((len(days) - 260) * 0.6)]
    end = days[-1] + DAY
    iso = lambda s: datetime.fromtimestamp(s, tz=timezone.utc).strftime("%Y-%m-%d")

    def bench(sym):
        c = [(days[i], prices[sym][i] / prices[sym][260] * config.STARTING_CASH) for i in range(260, len(days))]
        return c

    spy = bench("SPY")
    rows, curves = [], {}
    variants = grid()
    for p in variants:
        res = simulate(days, prices, p)
        curves[p.name] = res["curve"]
        rows.append({
            "name": p.name, "params": p.__dict__,
            "train": period_stats(res["curve"], first, split),
            "test": period_stats(res["curve"], split, end),
            "full": period_stats(res["curve"], first, end),
            "trades": res["trades"],
        })
        log.info("%s: allenamento %+.0f%% verifica %+.0f%%", p.name, 100 * rows[-1]["train"]["return"], 100 * rows[-1]["test"]["return"])
    best = max(rows, key=lambda r: r["train"]["cagr"])
    # La stessa regola su ogni tipo di prodotto: gli ETF sono il test senza "senno di poi".
    by_kind = []
    for universe in ("solo ETF", "solo azioni", "solo crypto"):
        params = MultiParams(f"{best['name']}, {universe}", **{**{k: v for k, v in best["params"].items() if k not in ("name", "universe")}, "universe": universe})
        res = simulate(days, prices, params)
        by_kind.append({"universe": universe, "train": period_stats(res["curve"], first, split),
                        "test": period_stats(res["curve"], split, end), "full": period_stats(res["curve"], first, end)})
    report = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "start": iso(first), "split": iso(split), "end": iso(days[-1]),
        "products": len(data),
        "spy": {"train": period_stats(spy, first, split), "test": period_stats(spy, split, end), "full": period_stats(spy, first, end)},
        "variants": rows,
        "best": best["name"],
        "by_kind": by_kind,
        "curve": [{"time": iso(t), "best": round(e, 2), "spy": round(spy[k][1], 2)}
                  for k, (t, e) in enumerate(curves[best["name"]]) if k % 5 == 0],
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "multi_backtest.json").write_text(json.dumps(report, indent=1))
    return report


# ---------------- dal vivo ----------------

def quote(symbol: str) -> float | None:
    """Ultimo prezzo disponibile (durante la borsa aperta è quello del momento)."""
    try:
        r = requests.get(YAHOO + symbol, headers=HEADERS, timeout=20, params={"range": "1d", "interval": "5m"})
        r.raise_for_status()
        return float(r.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except (requests.RequestException, KeyError, TypeError, IndexError, ValueError):
        return None


def paths(state_dir: Path) -> dict[str, Path]:
    d = state_dir / "multi"
    return {"dir": d, "state": d / "state.json", "trades": d / "trades.csv", "equity": d / "equity.csv", "meta": d / "meta.json"}


def run_live(state_dir: Path, out_dir: Path, now_s: int | None = None) -> None:
    import csv

    from .portfolio import Broker, State, iso, read_trades

    now_s = now_s or int(time.time())
    p = paths(state_dir)
    p["dir"].mkdir(parents=True, exist_ok=True)
    state = State.load(p["state"])
    broker = Broker(state, p["trades"])
    meta = json.loads(p["meta"].read_text()) if p["meta"].exists() else {"last_rebalance": 0}

    prices = {}
    for sym in state.positions:
        q = quote(sym)
        if q:
            prices[sym] = q
    hour = datetime.fromtimestamp(now_s, tz=timezone.utc).hour
    due = now_s - meta["last_rebalance"] >= REBALANCE_DAYS * DAY and hour >= DECISION_HOUR_UTC
    if due:
        data = load_universe(UNIVERSE, fresh=True)
        days, series = matrix(data)
        ema200 = {s: ema_series(series[s], 200) for s in series}
        i = len(days) - 1
        target = pick(series, ema200, i, LIVE, list(series))
        last = {s: series[s][i] for s in series if series[s][i]}
        prices.update({s: last[s] for s in target if s in last})
        t_ms = now_s * 1000
        for sym in list(state.positions):
            if sym not in target:
                broker.sell(sym, prices.get(sym) or last.get(sym, state.positions[sym].entry_price), t_ms, "uscito dai 10 più forti")
        equity = state.equity(prices)
        for sym in target:
            if sym not in state.positions and sym in last:
                broker.buy(sym, last[sym], 0.0, t_ms, equity, "tra i 10 più forti dell'ultimo anno", fraction=1 / LIVE.top)
        meta = {"last_rebalance": now_s, "target": target}
        p["meta"].write_text(json.dumps(meta))
        log.info("multi-mercato: ribilanciato, ora tiene %s", ", ".join(target))

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
    next_due = (meta["last_rebalance"] + REBALANCE_DAYS * DAY) if meta["last_rebalance"] else now_s
    summary = [{
        "updated": iso(now_s * 1000), "start_cash": config.STARTING_CASH, "equity": round(equity, 2),
        "cash": round(state.cash, 2), "open_positions": len(state.positions), "closed_trades": len(sells),
        "fees_paid": round(sum(float(t["fee"]) for t in trades), 2),
        "next_rebalance": datetime.fromtimestamp(next_due, tz=timezone.utc).strftime("%Y-%m-%d"),
        "strategy": LIVE.name,
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
    for name, rows in [("multi_summary", summary), ("multi_positions", positions), ("multi_trades", trade_rows), ("multi_equity", eq_rows[-3000:])]:
        (out_dir / f"{name}.json").write_text(json.dumps(rows, indent=1))
