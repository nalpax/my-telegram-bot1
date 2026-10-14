#!/usr/bin/env bash
set -euo pipefail
cd /workspace/my-telegram-bot1
python -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'
if [ ! -x .venv/bin/python ]; then
  python -m venv .venv
fi
.venv/bin/python -m pip install --cache-dir /workspace/.cache/pip -r requirements.txt
.venv/bin/python -m pip check --cache-dir /workspace/.cache/pip
.venv/bin/python -m pytest -q
.venv/bin/python -m gift_radar check
