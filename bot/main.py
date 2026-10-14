"""Keep `python -m bot.main` working after replacing the shop."""
from gift_radar.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["bot"]))
