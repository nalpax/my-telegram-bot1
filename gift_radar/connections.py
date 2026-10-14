"""Connection metadata only. Credentials remain in the host environment."""
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import MARKETS, safe_url


MARKET_LINKS = {
    "portals": {"open": "https://t.me/portals", "support": "https://t.me/portals_help_bot"},
    "tonnel": {"open": "https://market.tonnel.network/", "access": "https://t.me/thermos",
               "docs": "https://docs.swap.coffee/technical-guides/thermos/backend-api"},
    "mrkt": {"open": "https://t.me/mrkt"},
}
ACCESS_REQUEST = ("Здравствуйте! Нужен разрешённый read-only API для аналитики подарков: активные лоты и завершённые продажи. "
                  "Подскажите, как получить доступ, документацию, лимиты и условия использования. Торговых операций не будет.")


def is_operator(user_id: int) -> bool:
    names = os.environ.get("GIFT_RADAR_ADMIN_IDS") or os.environ.get("ADMIN_IDS", "")
    ids = {int(x.strip()) for x in names.split(",") if x.strip().isdigit()}
    return user_id in ids


def key_name(market, value):
    if market not in MARKETS or not isinstance(value, str) or not re.fullmatch(market.upper() + r"_[A-Z0-9_]{1,64}", value):
        raise ValueError("Use a market-prefixed environment variable NAME, never its value")
    return value


def documentation_url(value):
    value = safe_url(value)
    if value is None or len(value) > 1000 or urlsplit(value).query:
        raise ValueError("Public documentation URL without query credentials required")
    return value


@dataclass(frozen=True)
class Connection:
    market: str
    key_env: str | None = None
    docs_url: str | None = None

    def __post_init__(self):
        if self.market not in MARKETS:
            raise ValueError("Invalid market")
        if self.key_env:
            key_name(self.market, self.key_env)
        if self.docs_url:
            documentation_url(self.docs_url)

    @property
    def credential_present(self):
        # A proxy placeholder counts as present; presence never proves API authorization.
        return bool(self.key_env and os.environ.get(self.key_env))

    def diagnosis(self):
        if self.key_env and not self.credential_present:
            return "needs_credentials", "Переменная ключа сохранена, но не задана на сервере."
        if self.credential_present:
            return "needs_adapter", "Ключ задан на сервере. Запросы к официальному API пока не поддерживаются; проверка самого API не выполнена."
        if self.docs_url:
            return "needs_adapter", "Ссылка на документацию сохранена. Для получения данных нужно реализовать описанный в ней формат API."
        return "disabled", "Официальный разрешённый API не подтверждён. Получите доступ или загрузите разрешённый экспорт."
