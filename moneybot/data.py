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


def _get(symbol: str, params: dict) -> list:
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(BASE_URL, params={"symbol": symbol, **params}, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_error = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"impossibile scaricare {symbol}: {last_error}")


def fetch_candles(symbol: str, interval: str, limit: int) -> list[Candle]:
    """Scarica le ultime `limit` candele (a blocchi da 1000) e tiene solo quelle già chiuse."""
    rows: list = []
    end_time = None
    while len(rows) < limit:
        params = {"interval": interval, "limit": min(1000, limit - len(rows))}
        if end_time is not None:
            params["endTime"] = end_time
        batch = _get(symbol, params)
        if not batch:
            break
        rows = batch + rows
        end_time = int(batch[0][0]) - 1
        if len(batch) < params["limit"]:
            break

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
