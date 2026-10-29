"""Altre famiglie di strategie per il backtest, basate su "pesi obiettivo".

Ogni strategia, quando è il suo momento di decidere, dice quale quota del capitale
vuole in ogni moneta; il simulatore compra e vende la differenza pagando commissioni
e slippage. Le varianti si scelgono e si giudicano come in backtest.py.
"""

from dataclasses import dataclass, field
from typing import Callable

from . import config

HOUR_MS = 3600 * 1000
MIN_TRADE = 0.02  # non ribilancia differenze sotto il 2% del capitale


@dataclass(frozen=True)
class Strategy:
    name: str
    family: str
    description: str
    every_hours: int                      # ogni quante ore decide
    decide: Callable = field(compare=False)  # (series, i_by_sym, state) -> {simbolo: peso}


def _ok(s, i, need: int) -> bool:
    return i is not None and i >= need


def btc_trend(symbols: list[str], period: int):
    """Tiene le monete indicate (in parti uguali) solo quando sono sopra la loro media."""
    def decide(series, idx, state):
        held = [sym for sym in symbols if _ok(series.get(sym), idx.get(sym), period)
                and series[sym].close[idx[sym]] > series[sym].emas[period][idx[sym]]]
        return {sym: 1 / len(symbols) for sym in held}
    return decide


def momentum(top: int, lookback: int, btc_filter: bool):
    """Ogni volta compra le `top` monete salite di più nelle ultime `lookback` ore (se in salita)."""
    def decide(series, idx, state):
        btc, bi = series.get("BTC"), idx.get("BTC")
        if btc_filter and not (_ok(btc, bi, 1200) and btc.close[bi] > btc.emas[1200][bi]):
            return {}
        ranked = []
        for sym, s in series.items():
            i = idx.get(sym)
            if i is None or i < lookback:
                continue
            ret = s.close[i] / s.close[i - lookback] - 1
            if ret > 0:
                ranked.append((ret, sym))
        best = [sym for _, sym in sorted(ranked, reverse=True)[:top]]
        return {sym: 1 / top for sym in best}
    return decide


def dip_buy(symbols: list[str], drop: float, max_hours: int, stop: float):
    """Compra un forte ribasso sotto la media a 200 ore, in un mercato che a 50 giorni sale;
    esce quando il prezzo torna alla media, dopo `max_hours` ore o con lo stop."""
    def decide(series, idx, state):
        weights = {}
        for sym in symbols:
            s, i = series.get(sym), idx.get(sym)
            if not _ok(s, i, 1200):
                continue
            pos = state.get(sym)
            price, mean = s.close[i], s.emas[200][i]
            if pos:
                if price >= mean or i - pos["i"] >= max_hours or price <= pos["price"] * (1 - stop):
                    state.pop(sym)
                    continue
                weights[sym] = 1 / len(symbols)
            elif price < mean * (1 - drop) and price > s.emas[1200][i] * (1 - 2 * drop):
                state[sym] = {"i": i, "price": price}
                weights[sym] = 1 / len(symbols)
        return weights
    return decide


def fast_trend(top: int):
    """Strategia veloce: ogni 4 ore tiene le monete con media a 20 ore sopra quella a 50."""
    def decide(series, idx, state):
        picks = []
        for sym, s in series.items():
            i = idx.get(sym)
            if i is None or i < 200:
                continue
            if s.emas[20][i] > s.emas[50][i] and s.close[i] > s.emas[200][i]:
                picks.append((s.close[i] / s.close[i - 24] - 1, sym))
        return {sym: 1 / top for _, sym in sorted(picks, reverse=True)[:top]}
    return decide


def day_momentum(top: int):
    """Strategia veloce: ogni giorno compra le `top` monete salite di più nelle ultime 24 ore."""
    def decide(series, idx, state):
        ranked = [(s.close[idx[sym]] / s.close[idx[sym] - 24] - 1, sym)
                  for sym, s in series.items() if idx.get(sym) is not None and idx[sym] >= 24]
        return {sym: 1 / top for r, sym in sorted(ranked, reverse=True)[:top] if r > 0}
    return decide


STRATEGIES = [
    Strategy("solo BTC in trend", "trend su BTC", "Tiene BTC solo quando è sopra la sua media di 50 giorni; altrimenti contanti.", 24,
             btc_trend(["BTC"], 1200)),
    Strategy("BTC+ETH in trend", "trend su BTC", "BTC ed ETH metà e metà, ciascuno solo quando è sopra la sua media di 50 giorni.", 24,
             btc_trend(["BTC", "ETH"], 1200)),
    Strategy("BTC+ETH trend veloce", "trend su BTC", "Come sopra ma con la media di 8 giorni: entra ed esce prima.", 24,
             btc_trend(["BTC", "ETH"], 200)),
    Strategy("momentum 3 su 30 giorni", "momentum", "Ogni settimana tiene le 3 monete salite di più in 30 giorni, solo se BTC è in trend.", 168,
             momentum(3, 720, True)),
    Strategy("momentum 3 su 14 giorni", "momentum", "Ogni settimana tiene le 3 monete salite di più in 14 giorni, solo se BTC è in trend.", 168,
             momentum(3, 336, True)),
    Strategy("momentum 5 senza filtro", "momentum", "Ogni settimana tiene le 5 monete salite di più in 30 giorni, sempre.", 168,
             momentum(5, 720, False)),
    Strategy("compra i ribassi BTC/ETH", "ribassi", "Compra BTC/ETH quando scendono del 4% sotto la media a 200 ore; esce al ritorno alla media. È il capitale rapido.", 1,
             dip_buy(["BTC", "ETH"], 0.04, 120, 0.10)),
    Strategy("compra i ribassi BTC/ETH al -6%", "ribassi", "Come sopra ma con soglia al 6%: la prima versione del capitale rapido.", 1,
             dip_buy(["BTC", "ETH"], 0.06, 120, 0.10)),
    Strategy("compra i ribassi (8 monete)", "ribassi", "Come sopra sulle 8 monete più grandi, ribasso del 8%.", 1,
             dip_buy(["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "LINK"], 0.08, 120, 0.12)),
    Strategy("veloce: trend 4 ore", "veloce", "Ogni 4 ore tiene le 3 monete con trend orario rialzista più forte.", 4,
             fast_trend(3)),
    Strategy("veloce: le migliori di ieri", "veloce", "Ogni giorno compra le 3 monete salite di più nelle ultime 24 ore.", 24,
             day_momentum(3)),
]


def simulate_weights(series, times: list[int], strat: Strategy) -> dict:
    fee = config.FEE_RATE + config.SLIPPAGE_RATE
    cash = config.STARTING_CASH
    qty: dict[str, float] = {}
    state: dict = {}
    curve, trades = [], []
    last_price: dict[str, float] = {}
    open_cost: dict[str, float] = {}
    for k, t in enumerate(times):
        idx = {sym: s.index.get(t) for sym, s in series.items()}
        for sym, i in idx.items():
            if i is not None:
                last_price[sym] = series[sym].close[i]
        equity = cash + sum(q * last_price[sym] for sym, q in qty.items())
        if k % strat.every_hours == 0:
            target = strat.decide(series, idx, state)
            for sym in set(qty) | set(target):
                if sym not in last_price:
                    continue
                price = last_price[sym]
                want = target.get(sym, 0.0) * equity
                have = qty.get(sym, 0.0) * price
                delta = want - have
                if abs(delta) < MIN_TRADE * equity and not (want == 0 and have > 0):
                    continue
                if delta > 0:
                    delta = min(delta, cash)
                    cash -= delta
                    qty[sym] = qty.get(sym, 0.0) + delta * (1 - fee) / price
                    open_cost[sym] = open_cost.get(sym, 0.0) + delta
                elif delta < 0:
                    sell_q = min(qty.get(sym, 0.0), -delta / price)
                    proceeds = sell_q * price * (1 - fee)
                    share = sell_q / qty[sym]
                    cost = open_cost.get(sym, 0.0) * share
                    cash += proceeds
                    qty[sym] -= sell_q
                    open_cost[sym] = open_cost.get(sym, 0.0) - cost
                    if qty[sym] * price < 1:
                        qty.pop(sym)
                        open_cost.pop(sym, None)
                    trades.append({"symbol": sym, "exit": t, "pnl": proceeds - cost, "ret": proceeds / cost - 1 if cost else 0.0})
            equity = cash + sum(q * last_price[sym] for sym, q in qty.items())
        curve.append((t, equity))
    return {"curve": curve, "trades": trades}
