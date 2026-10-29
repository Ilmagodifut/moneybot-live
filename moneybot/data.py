"""Prezzi reali dal servizio pubblico di Binance (nessun account richiesto)."""

import time
from dataclasses import dataclass

import requests

BASE_URL = "https://data-api.binance.vision/api/v3/klines"


@dataclass
class Candle:
    open_time: int   # millisecondi
    open: float
    high: float
    low: float
    close: float
    volume: float    # volume in USDT
    close_time: int  # millisecondi


def fetch_candles(symbol: str, interval: str, limit: int) -> list[Candle]:
    """Scarica le candele e tiene solo quelle già chiuse."""
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(
                BASE_URL,
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=20,
            )
            resp.raise_for_status()
            rows = resp.json()
            break
        except requests.RequestException as e:
            last_error = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"impossibile scaricare {symbol}: {last_error}")

    now_ms = int(time.time() * 1000)
    candles = [
        Candle(
            open_time=int(r[0]),
            open=float(r[1]),
            high=float(r[2]),
            low=float(r[3]),
            close=float(r[4]),
            volume=float(r[7]),
            close_time=int(r[6]),
        )
        for r in rows
    ]
    return [c for c in candles if c.close_time < now_ms]
