"""Download the public Thermos OpenAPI description. No key, no auth header, Thermos host only."""
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from httpx import AsyncClient, HTTPError, TimeoutException

HOST = "backend.thermos.gifts"
SWAGGER_UI = f"https://{HOST}/swagger-ui"
# Common spec locations, tried only if the Swagger UI page does not name its spec.
FALLBACKS = ("/openapi.json", "/api/openapi.json", "/api/v1/openapi.json", "/v3/api-docs")
LIMIT = 5 * 1024 * 1024
INTERESTING = ("catalog", "market", "search", "activity", "history", "sale", "listing", "floor", "collection")


@dataclass
class SpecResult:
    state: str
    message: str
    content: bytes = b""
    url: str = ""
    paths: list = field(default_factory=list)


def same_host(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == HOST and not parts.username and not parts.password


async def fetch(client, url):
    """GET with redirects followed manually and only inside the Thermos host."""
    for _ in range(4):
        if not same_host(url):
            raise ValueError("Off-host location")
        async with client.stream("GET", url) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers.get("location", ""))
                continue
            body = bytearray()
            async for chunk in response.aiter_bytes(65536):
                body.extend(chunk)
                if len(body) > LIMIT:
                    raise ValueError("Response too large")
            return response.status_code, bytes(body), url
    raise ValueError("Too many redirects")


def candidates(page, base):
    found = re.findall(r"""(?:url|configUrl)\s*[:=]\s*["']([^"'\s]+)["']""", page)
    found += re.findall(r"""["']([^"'\s]*(?:openapi|api-docs|swagger)[^"'\s]*\.(?:json|yaml))["']""", page)
    seen = []
    for value in [*found, *FALLBACKS]:
        url = urljoin(base, value)
        if same_host(url) and url not in seen:
            seen.append(url)
    return seen


def summarize(spec):
    paths = []
    for path, operations in (spec.get("paths") or {}).items():
        if not isinstance(operations, dict):
            continue
        for method in operations:
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                paths.append(f"{method.upper()} {path}")
    return paths


async def download_spec(transport=None) -> SpecResult:
    try:
        async with AsyncClient(timeout=15, follow_redirects=False, transport=transport,
                               headers={"Accept": "application/json, text/html"}) as client:
            status, page, base = await fetch(client, SWAGGER_UI)
            text = page.decode("utf-8", "replace") if status == 200 else ""
            for url in candidates(text, base):
                status, body, final = await fetch(client, url)
                if status != 200:
                    continue
                try:
                    spec = json.loads(body)
                except (ValueError, UnicodeError, RecursionError):
                    continue
                if isinstance(spec, dict) and isinstance(spec.get("paths"), dict):
                    return SpecResult("ok", "Описание API получено.", body, final, summarize(spec))
                if isinstance(spec, dict) and isinstance(spec.get("urls"), list):
                    # Swagger config listing one or more specs.
                    for item in spec["urls"]:
                        target = urljoin(final, item.get("url", "")) if isinstance(item, dict) else ""
                        if same_host(target):
                            status, body, final2 = await fetch(client, target)
                            try:
                                inner = json.loads(body) if status == 200 else None
                            except (ValueError, UnicodeError, RecursionError):
                                inner = None
                            if isinstance(inner, dict) and isinstance(inner.get("paths"), dict):
                                return SpecResult("ok", "Описание API получено.", body, final2, summarize(inner))
            return SpecResult("not_found", "Не удалось найти файл описания API на сервере Thermos.")
    except (TimeoutError, TimeoutException):
        return SpecResult("timeout", "Thermos не ответил вовремя. Повтори позже.")
    except (HTTPError, OSError):
        return SpecResult("network_error", "Нет соединения с backend.thermos.gifts. Повтори позже.")
    except ValueError:
        return SpecResult("unexpected_response", "Сервер Thermos ответил в неожиданном формате.")
