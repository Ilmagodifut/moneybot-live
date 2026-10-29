"""Uso:
    python -m moneybot run       esegue un giro (controlla posizioni, cerca entrate, aggiorna il report)
    python -m moneybot resume    riattiva il bot dopo uno stop di sicurezza
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
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
