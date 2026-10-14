"""Compatibility with existing shop-host settings; only synthetic tokens."""
import os
import subprocess
import sys
from pathlib import Path

from gift_radar.config import bot_token, database_path, initialize_environment


def test_existing_dotenv_alias_and_data_dir(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("GIFT_RADAR_DB", raising=False)
    monkeypatch.delenv("DATA_DIR", raising=False)
    (tmp_path / ".env").write_text('BOT_TOKEN=123:TEST_ONLY_NOT_REAL\nDATA_DIR=persistent\n')
    initialize_environment()
    assert bot_token() == "123:TEST_ONLY_NOT_REAL"
    assert database_path() == str(Path("persistent") / "radar.sqlite3")


def test_injected_token_takes_priority(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BOT_TOKEN", "123:TEST_PROCESS_TOKEN")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:TEST_CANONICAL_TOKEN")
    (tmp_path / ".env").write_text('BOT_TOKEN=123:TEST_FILE_TOKEN\nTELEGRAM_BOT_TOKEN=123:TEST_FILE_TOKEN\n')
    initialize_environment()
    assert bot_token() == "123:TEST_CANONICAL_TOKEN"


def test_host_entry_points_missing_token(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = {k:v for k,v in os.environ.items() if k not in ("BOT_TOKEN", "TELEGRAM_BOT_TOKEN")}
    environment.update(PYTHONPATH=str(root), GIFT_RADAR_DB=str(tmp_path / "test.sqlite3"))
    for args in ([sys.executable,str(root / "main.py")], [sys.executable,"-m","bot.main"]):
        result = subprocess.run(args,cwd=tmp_path,env=environment,capture_output=True,text=True,timeout=10)
        assert result.returncode == 2
        assert "Не задан" in result.stdout
        assert "ModuleNotFoundError" not in result.stderr
