"""Tutte le impostazioni del bot in un solo posto.

Le soglie sono un punto di partenza, non verità: vanno verificate
con il paper trading prima di fidarsi.
"""

import os
from pathlib import Path

# Monete seguite (coppie contro USDT).
SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "LINK",
    "DOT", "LTC", "TRX", "SUI", "NEAR", "ATOM", "UNI", "TON", "APT",
]
QUOTE = "USDT"

# Dati di mercato: candele orarie, abbastanza per una EMA a 200.
INTERVAL = "1h"
CANDLE_LIMIT = 300

# Portafoglio simulato.
STARTING_CASH = 1000.0
POSITION_FRACTION = 0.10   # ogni posizione = 10% del capitale totale
MAX_OPEN_POSITIONS = 5
FEE_RATE = 0.001           # 0,1% per lato, come un exchange tipico
SLIPPAGE_RATE = 0.0005     # 0,05% di prezzo peggiore a ogni esecuzione

# Uscite.
STOP_ATR = 2.0             # stop iniziale = entrata - 2 ATR...
STOP_MIN_PCT = 0.03        # ...ma almeno il 3% sotto, per non uscire sul rumore
TRAIL_ATR = 3.0            # trailing stop = massimo raggiunto - 3 ATR...
TRAIL_MIN_PCT = 0.04       # ...ma almeno il 4% sotto il massimo
COOLDOWN_HOURS = 12        # dopo un'uscita, niente rientri sulla stessa moneta

# Freni di sicurezza.
DAILY_LOSS_LIMIT = 0.03    # -3% nel giorno: stop ai nuovi acquisti fino a domani
MAX_DRAWDOWN = 0.25        # -25% dal massimo: bot fermo finché non lo riattivi

# Il giudice (Claude). Senza chiave il bot usa solo le regole.
# Sonnet: buon equilibrio tra qualità e costo (circa 1 centesimo a valutazione).
JUDGE_MODEL = os.environ.get("MONEYBOT_MODEL") or "claude-sonnet-5-5"
JUDGE_EFFORT = os.environ.get("MONEYBOT_EFFORT") or "low"
MAX_JUDGE_CALLS_PER_RUN = 3
MAX_JUDGE_CALLS_PER_DAY = 36   # tetto di spesa: al massimo circa 0,40 $ al giorno con Sonnet

# Il cancello: Claude risponde, il codice decide.
GATE_ACTION_MIN_PROB = 0.65
GATE_HEALTHY_MIN_PROB = 0.60
GATE_MAX_MARKET_RISK = 3

# Dove vengono salvati stato, operazioni e verdetti.
ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("MONEYBOT_STATE_DIR", ROOT / "state"))
REPORT_PATH = Path(os.environ.get("MONEYBOT_REPORT", ROOT / "REPORT.md"))


def judge_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
