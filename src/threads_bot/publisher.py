"""Publishers: dry-run by default, Threads API only when explicitly selected.

The Threads endpoints below come from search results about Meta's documentation and have
NOT yet been verified with a real call (see docs/phase-0.md). Never log request URLs or
parameters here: they carry the access token.
"""

from dataclasses import dataclass
import json
import time
from typing import Callable, Protocol
from urllib import error as urlerror, parse, request


class PublishError(Exception):
    """Base class. `stage` is "container" (nothing posted) or "publish" (outcome may be unknown)."""

    def __init__(self, message: str, stage: str = "container") -> None:
        super().__init__(message)
        self.stage = stage


class AuthError(PublishError):
    """Token missing, expired or lacking permission. Retrying will not help."""


class TransientError(PublishError):
    """Network failure, rate limit or server error. A later run may succeed."""


class PermanentError(PublishError):
    """The request was rejected and will be rejected again as-is."""


@dataclass(frozen=True)
class Quota:
    used: int
    total: int


class Publisher(Protocol):
    def publish(self, text: str, reply_to_id: str | None = None) -> str: ...

    def quota(self) -> Quota | None: ...


class DryRunPublisher:
    """Records what would be posted. Makes no network calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def publish(self, text: str, reply_to_id: str | None = None) -> str:
        self.calls.append((text, reply_to_id))
        return f"dry-run-{len(self.calls)}"

    def quota(self) -> Quota | None:
        return None


Transport = Callable[[str, str, dict[str, str]], dict]

API_BASE = "https://graph.threads.net/v1.0"  # Unverified; confirm in Phase 0.
NOT_READY_STATUSES = {"IN_PROGRESS"}
FAILED_STATUSES = {"ERROR", "EXPIRED"}


def urllib_transport(method: str, url: str, params: dict[str, str], timeout: float = 30) -> dict:
    body = parse.urlencode(params)
    if method == "GET":
        req = request.Request(f"{url}?{body}", method="GET")
    else:
        req = request.Request(url, data=body.encode(), method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urlerror.HTTPError as http_error:
        try:
            payload = json.loads(http_error.read().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = {}
        raise classify_http_error(http_error.code, payload) from None
    except (urlerror.URLError, TimeoutError, ConnectionError) as network_error:
        raise TransientError(f"network: {type(network_error).__name__}") from None


def classify_http_error(status: int, payload: dict) -> PublishError:
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    code = error.get("code")
    message = str(error.get("message", ""))[:200]
    summary = f"HTTP {status}" + (f" code {code}" if code is not None else "") + (f": {message}" if message else "")
    if status in (401, 403) or code == 190:
        return AuthError(summary)
    if status == 429 or status >= 500:
        return TransientError(summary)
    return PermanentError(summary)


class ThreadsPublisher:
    """Text posts via container creation → status check → threads_publish."""

    def __init__(
        self,
        user_id: str,
        access_token: str,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        status_checks: int = 5,
        status_wait: float = 3.0,
    ) -> None:
        if not user_id or not access_token:
            raise AuthError("THREADS_USER_ID and THREADS_ACCESS_TOKEN are required")
        self._user_id = user_id
        self._token = access_token
        self._transport = transport
        self._sleep = sleep
        self._status_checks = status_checks
        self._status_wait = status_wait

    def _call(self, method: str, path: str, params: dict[str, str], stage: str) -> dict:
        try:
            return self._transport(method, f"{API_BASE}/{path}", {**params, "access_token": self._token})
        except PublishError as error:
            error.stage = stage
            raise

    def publish(self, text: str, reply_to_id: str | None = None) -> str:
        params = {"media_type": "TEXT", "text": text}
        if reply_to_id:
            params["reply_to_id"] = reply_to_id
        container = self._call("POST", f"{self._user_id}/threads", params, "container")
        container_id = container.get("id")
        if not container_id:
            raise PermanentError("container response has no id")
        self._wait_until_ready(str(container_id))
        published = self._call("POST", f"{self._user_id}/threads_publish", {"creation_id": str(container_id)}, "publish")
        post_id = published.get("id")
        if not post_id:
            raise PermanentError("publish response has no id", stage="publish")
        return str(post_id)

    def _wait_until_ready(self, container_id: str) -> None:
        for attempt in range(self._status_checks):
            status = self._call("GET", container_id, {"fields": "status,error_message"}, "container").get("status")
            if status in FAILED_STATUSES:
                raise PermanentError(f"container status {status}")
            if status not in NOT_READY_STATUSES:
                return  # FINISHED, or a status field this code does not know yet.
            if attempt + 1 < self._status_checks:
                self._sleep(self._status_wait)
        raise TransientError("container not ready in time")

    def quota(self) -> Quota | None:
        """Best effort; the response shape is unverified, so unknown shapes return None."""
        try:
            data = self._call(
                "GET", f"{self._user_id}/threads_publishing_limit", {"fields": "quota_usage,config"}, "container"
            )
            entry = data["data"][0]
            return Quota(int(entry["quota_usage"]), int(entry["config"]["quota_total"]))
        except (KeyError, IndexError, TypeError, ValueError, TransientError, PermanentError):
            return None
