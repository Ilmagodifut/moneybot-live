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
    trend_fast: float        # media mobile a config.TREND_FAST ore
    trend_mid: float
    trend_slow: float
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
    if len(candles) < config.TREND_SLOW:
        return None
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    vols = [c.volume for c in candles]
    e20 = ema(closes, 20)
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
        trend_fast=ema(closes, config.TREND_FAST)[-1],
        trend_mid=ema(closes, config.TREND_MID)[-1],
        trend_slow=ema(closes, config.TREND_SLOW)[-1],
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
    """Trend rialzista su giorni e settimane, slancio presente ma non esagerato."""
    return (
        s.close > s.trend_slow
        and s.trend_fast > s.trend_mid > s.trend_slow
        and 50 <= s.rsi14 <= 70
    )


def rules_only_gate(s: Snapshot) -> bool:
    """Il cancello usato quando Claude non è configurato."""
    return is_candidate(s) and s.dist_ema20_atr < 2.0 and s.volume_ratio >= 0.8


def trend_broken(s: Snapshot) -> bool:
    return s.trend_fast < s.trend_mid


def trend_label(s: Snapshot) -> str:
    if s.trend_fast > s.trend_mid > s.trend_slow:
        return "up"
    if s.trend_fast < s.trend_mid < s.trend_slow:
        return "down"
    return "sideways"
