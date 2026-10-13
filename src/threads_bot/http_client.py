"""Small HTTPS client. Credentials never appear in URLs or error messages."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ServiceError(RuntimeError):
    pass


def request_bytes(url: str, *, payload=None, token=None, timeout=45) -> bytes:
    headers = {"User-Agent": "threads-trend-bot/0.2"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=timeout) as response:
            return response.read()
    except HTTPError as error:
        raise ServiceError(f"Service returned HTTP {error.code}") from None
    except (URLError, TimeoutError):
        raise ServiceError("Service connection failed; check network access") from None


def request_json(url: str, **kwargs):
    try:
        return json.loads(request_bytes(url, **kwargs))
    except (ValueError, UnicodeError):
        raise ServiceError("Service returned invalid JSON") from None
