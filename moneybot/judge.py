"""Il giudice: Claude risponde a domande chiuse con una probabilità.

Claude non decide nulla da solo. Riceve solo fatti, sceglie tra opzioni
già scritte e il codice applica il cancello (vedi config.GATE_*).
Se Claude non risponde, il verdetto è "pass": nessuna risposta non è un sì.
"""

import json
import logging
from dataclasses import dataclass

import anthropic

from . import config
from .strategy import Snapshot

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the risk judge on a paper-trading desk for spot crypto (no leverage).
You receive a dossier of facts about one coin plus the BTC market context, measured on hourly candles.
Answer the four closed questions using only the dossier. Do not invent data; if a fact is missing, lower your probability.
Each probability is how likely your chosen answer is correct, between 0 and 1. Be calibrated: when you say 0.8 you should be right about 80% of the time.
Most setups should get "pass" or "watch". Missing a good trade costs nothing; taking a bad one costs money.

Questions:
1. trend_healthy (yes/no): price is rising steadily on broad participation (rising volume, higher lows), not a single spike.
2. overextended (yes/no): most of the move already happened; price is stretched far above its averages and a pullback is likely before further gains.
3. market_risk (1-5): risk from the overall market right now. 1 = BTC calm and trending up, 5 = BTC falling hard or very volatile.
4. action (buy/watch/pass): buy = clean setup, enter now; watch = interesting but not yet; pass = no.
Keep "reason" to one short sentence citing the facts that decided it."""

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "trend_healthy": {"type": "string", "enum": ["yes", "no"]},
        "trend_healthy_prob": {"type": "number"},
        "overextended": {"type": "string", "enum": ["yes", "no"]},
        "overextended_prob": {"type": "number"},
        "market_risk": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "action": {"type": "string", "enum": ["buy", "watch", "pass"]},
        "action_prob": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": [
        "trend_healthy", "trend_healthy_prob", "overextended", "overextended_prob",
        "market_risk", "action", "action_prob", "reason",
    ],
    "additionalProperties": False,
}


class JudgeUnavailable(Exception):
    """Claude non può rispondere a nessuna richiesta (chiave non valida, credito finito)."""


@dataclass
class Verdict:
    trend_healthy: str
    trend_healthy_prob: float
    overextended: str
    overextended_prob: float
    market_risk: int
    action: str
    action_prob: float
    reason: str

    def passes_gate(self) -> bool:
        return (
            self.action == "buy"
            and self.action_prob >= config.GATE_ACTION_MIN_PROB
            and self.trend_healthy == "yes"
            and self.trend_healthy_prob >= config.GATE_HEALTHY_MIN_PROB
            and self.overextended == "no"
            and self.market_risk <= config.GATE_MAX_MARKET_RISK
        )


def build_dossier(s: Snapshot, btc: Snapshot | None) -> str:
    def fmt(x: Snapshot) -> str:
        return "\n".join([
            f"symbol: {x.symbol}",
            f"price: {x.close:.6g}",
            f"change 1h / 24h / 7d: {x.change_1h:+.2%} / {x.change_24h:+.2%} / {x.change_7d:+.2%}",
            f"EMA20 / EMA50 / EMA200: {x.ema20:.6g} / {x.ema50:.6g} / {x.ema200:.6g}",
            f"RSI14: {x.rsi14:.1f}",
            f"ATR14 (avg hourly range): {x.atr14:.6g} ({x.atr14 / x.close:.2%} of price)",
            f"price above EMA20, in ATRs: {x.dist_ema20_atr:.2f}",
            f"volume last 24h vs prior 7-day daily average: {x.volume_ratio:.2f}x",
            f"higher lows over the last 6 hours: {'yes' if x.higher_lows_6h else 'no'}",
        ])

    market = fmt(btc) if btc else "missing"
    return f"COIN\n{fmt(s)}\n\nMARKET CONTEXT (BTC)\n{market}"


def ask(client: anthropic.Anthropic, s: Snapshot, btc: Snapshot | None) -> Verdict | None:
    request = dict(
        model=config.JUDGE_MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        output_config={
            "effort": config.JUDGE_EFFORT,
            "format": {"type": "json_schema", "schema": VERDICT_SCHEMA},
        },
        messages=[{"role": "user", "content": build_dossier(s, btc)}],
    )
    try:
        if config.JUDGE_MODEL.startswith("claude-haiku"):
            # Haiku non ha il fallback lato server: un rifiuto diventa semplicemente "pass".
            response = client.messages.create(**request)
        else:
            response = client.beta.messages.create(
                **request, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        log.error("chiave Claude non valida: per questo giro decidono le regole")
        raise JudgeUnavailable from e
    except anthropic.BadRequestError as e:
        if "credit" in str(e).lower():
            log.error("credito Claude esaurito: per questo giro decidono le regole")
            raise JudgeUnavailable from e
        log.warning("Claude: richiesta rifiutata su %s, considerato pass", s.symbol)
        return None
    except anthropic.RateLimitError:
        log.warning("Claude: troppe richieste, %s considerato pass", s.symbol)
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude: errore %s su %s, considerato pass", e.status_code, s.symbol)
        return None
    except anthropic.APIConnectionError:
        log.warning("Claude non raggiungibile, %s considerato pass", s.symbol)
        return None

    if response.stop_reason == "refusal":
        log.warning("Claude ha rifiutato di rispondere su %s, considerato pass", s.symbol)
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        return None
    try:
        data = json.loads(text)
        return Verdict(**{k: data[k] for k in VERDICT_SCHEMA["required"]})
    except (json.JSONDecodeError, KeyError, TypeError):
        log.warning("risposta di Claude illeggibile su %s, considerato pass", s.symbol)
        return None
