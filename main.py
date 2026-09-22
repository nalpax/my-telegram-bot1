"""
Thin entry point for hosting platforms that expect a single root-level
"main file" (they typically run `python main.py` from the project root).

The actual application lives in bot/main.py and uses package-relative
imports (`from bot.config import ...`), so it must be launched as the
`bot.main` module with the project root on sys.path — not as a bare
script. Running `python bot/main.py` directly would fail with
`ModuleNotFoundError: No module named 'bot'`.

This file makes both work: `python main.py` (from the root) and
`python -m bot.main` (as used in the Dockerfile) end up running the
exact same code.
"""
import asyncio

from bot.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
