"""Regole fisse: quali monete sono candidate all'acquisto e quando uscire."""

from dataclasses import asdict, dataclass

from . import config
from .data import Candle
from .indicators import atr, ema, pct_change, rsi


@dataclass
class Snapshot:
    """I numeri di una moneta all'ultima candela chiusa."""
    symbol: str
    time: int
    close: float
    ema20: float
    ema50: float
    ema200: float
    rsi14: float
    atr14: float
    change_1h: float
    change_24h: float
    change_7d: float
    volume_24h: float
    volume_ratio: float      # volume ultime 24h / media giornaliera dei 7 giorni prima
    dist_ema20_atr: float    # quanti ATR il prezzo è sopra la EMA20
    higher_lows_6h: bool     # minimi delle ultime 6 ore in salita

    def to_dict(self) -> dict:
        return asdict(self)


def snapshot(symbol: str, candles: list[Candle]) -> Snapshot | None:
    if len(candles) < 200:
        return None
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    vols = [c.volume for c in candles]
    e20, e50, e200 = ema(closes, 20), ema(closes, 50), ema(closes, 200)
    a = atr(highs, lows, closes)[-1]
    vol_24h = sum(vols[-24:])
    prev_week = vols[-24 * 8:-24]
    avg_day = sum(prev_week) / (len(prev_week) / 24) if prev_week else vol_24h
    last_lows = lows[-6:]
    return Snapshot(
        symbol=symbol,
        time=candles[-1].close_time,
        close=closes[-1],
        ema20=e20[-1],
        ema50=e50[-1],
        ema200=e200[-1],
        rsi14=rsi(closes)[-1],
        atr14=a,
        change_1h=pct_change(closes[-1], closes[-2]),
        change_24h=pct_change(closes[-1], closes[-25]),
        change_7d=pct_change(closes[-1], closes[-169]),
        volume_24h=vol_24h,
        volume_ratio=vol_24h / avg_day if avg_day else 1.0,
        dist_ema20_atr=(closes[-1] - e20[-1]) / a if a else 0.0,
        higher_lows_6h=all(b >= x for x, b in zip(last_lows, last_lows[1:])),
    )


def is_candidate(s: Snapshot) -> bool:
    """Trend rialzista su più orizzonti, slancio presente ma non esagerato."""
    return (
        s.close > s.ema200
        and s.ema20 > s.ema50 > s.ema200
        and 50 <= s.rsi14 <= 70
    )


def rules_only_gate(s: Snapshot) -> bool:
    """Il cancello usato quando Claude non è configurato."""
    return is_candidate(s) and s.dist_ema20_atr < 2.0 and s.volume_ratio >= 0.8


def trend_broken(s: Snapshot) -> bool:
    return s.ema20 < s.ema50
