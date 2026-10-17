"""Encrypted marketplace credentials; the master key never goes into SQLite."""
import json
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .models import MARKETS

# Vault contexts: marketplaces plus the Thermos aggregator, which serves all three.
SOURCES = (*MARKETS, "thermos")
from .security import private_file


def credential_value(value):
    if not isinstance(value, str):
        raise ValueError("Invalid credential")
    value = value.strip()
    if (not 8 <= len(value) <= 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or re.fullmatch(r"(?:TONNEL|PORTALS|MRKT|THERMOS)_[A-Z0-9_]+", value)
            or re.search(r"\b[0-9]{6,12}:[A-Za-z0-9_-]{25,}\b", value)
            or value in {os.environ.get("BOT_TOKEN"), os.environ.get("TELEGRAM_BOT_TOKEN")}):
        raise ValueError("Invalid credential")
    return value


class CredentialVault:
    def __init__(self, database_path):
        self.path = None if database_path == ":memory:" else Path(str(database_path) + ".credentials.key")
        self.memory_key = Fernet.generate_key() if self.path is None else None

    def cipher(self, allow_create=False):
        try:
            configured = os.environ.get("GIFT_RADAR_ENCRYPTION_KEY")
            if configured:
                key = configured.encode("ascii")
            elif self.path is None:
                key = self.memory_key
            else:
                if allow_create and not self.path.exists():
                    try:
                        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        pass
                    else:
                        with os.fdopen(fd, "wb") as file:
                            file.write(Fernet.generate_key())
                            file.flush()
                            os.fsync(file.fileno())
                # Do not recreate a missing key when reading existing ciphertext.
                if not self.path.exists():
                    raise ValueError("Credential master key missing")
                private_file(self.path)
                fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as file:
                    key = file.read(128)
            return Fernet(key)
        except (OSError, ValueError, UnicodeError):
            raise ValueError("Credential vault unavailable; restore the master key") from None

    def encrypt(self, market, value, allow_create=False):
        if market not in SOURCES:
            raise ValueError("Invalid market")
        payload = json.dumps({"market": market, "key": credential_value(value)}).encode()
        return self.cipher(allow_create).encrypt(payload).decode("ascii")

    def decrypt(self, market, ciphertext):
        try:
            payload = json.loads(self.cipher().decrypt(ciphertext.encode("ascii")))
            if payload["market"] != market:
                raise ValueError("Credential context mismatch")
            return credential_value(payload["key"])
        except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
            raise ValueError("Credential cannot be decrypted; restore the master key") from None
