"""Uso:
    python -m moneybot run       esegue un giro (controlla posizioni, cerca entrate, aggiorna il report)
    python -m moneybot resume    riattiva il bot dopo uno stop di sicurezza
    python -m moneybot check     fa una domanda di prova a Claude (su BTC) senza fare operazioni
    python -m moneybot backtest  prova le regole su 2 anni di prezzi orari e salva i risultati
    python -m moneybot multi-backtest  prova il capitale multi-mercato su circa 20 anni
"""

import logging
import sys

from . import bot, config
from .portfolio import State


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "run":
        bot.run()
    elif command == "resume":
        path = bot.paths(config.STATE_DIR)["state"]
        state = State.load(path)
        state.halted = False
        state.halt_reason = ""
        state.peak_equity = state.cash + sum(p.cost for p in state.positions.values())
        state.save(path)
        print("bot riattivato")
    elif command == "backtest":
        from . import backtest
        report = backtest.run(out_dir=bot.paths(config.STATE_DIR)["dashboard"])
        print(f"migliore sul periodo di allenamento: {report['best']}")
    elif command == "multi-backtest":
        from . import multi
        report = multi.run_backtest(out_dir=bot.paths(config.STATE_DIR)["dashboard"])
        print(f"prodotti: {report['products']}, migliore in allenamento: {report['best']}")
    elif command == "check":
        check_claude()
    else:
        print(__doc__)
        sys.exit(2)


def check_claude() -> None:
    import anthropic

    from . import judge
    from .data import fetch_candles
    from .strategy import snapshot

    if not config.judge_enabled():
        sys.exit("ANTHROPIC_API_KEY non impostata")
    btc = snapshot("BTC", fetch_candles("BTC" + config.QUOTE, config.INTERVAL, config.CANDLE_LIMIT))
    try:
        verdict = judge.ask(anthropic.Anthropic(), btc, btc)
    except judge.JudgeUnavailable:
        sys.exit("Claude non disponibile: chiave non valida o credito esaurito")
    if verdict is None:
        sys.exit("Claude non ha dato una risposta valida")
    print(f"Claude risponde ({config.JUDGE_MODEL}): {verdict}")


if __name__ == "__main__":
    main()
