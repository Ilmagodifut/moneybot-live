"""Indicatori tecnici classici, scritti a mano per capire cosa calcolano."""


def ema(values: list[float], period: int) -> list[float]:
    """Media mobile esponenziale: dà più peso ai prezzi recenti."""
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int = 14) -> list[float]:
    """Relative Strength Index (0-100): sopra 70 'tirato', sotto 30 'depresso'."""
    if len(closes) <= period:
        return [50.0] * len(closes)
    gains, losses = [], []
    for prev, cur in zip(closes, closes[1:]):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    out = [50.0] * (period + 1)
    out[-1] = _rsi_value(avg_gain, avg_loss)
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        out.append(_rsi_value(avg_gain, avg_loss))
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float]:
    """Average True Range: quanto si muove il prezzo in media in una candela."""
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    if len(trs) < period:
        return [sum(trs) / len(trs)] * len(trs)
    first = sum(trs[:period]) / period
    out = [first] * period
    for tr in trs[period:]:
        out.append((out[-1] * (period - 1) + tr) / period)
    return out


def pct_change(new: float, old: float) -> float:
    return (new - old) / old if old else 0.0
