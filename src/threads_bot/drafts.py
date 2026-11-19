"""Publishing queue: one JSON file per draft under the queue directory."""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlparse

# Coupang requires this exact sentence on posts that carry Partners links.
COUPANG_DISCLOSURE = "이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."
# Short notice added to the post body so readers see it is an ad before opening the comment.
COUPANG_BODY_NOTICE = "(쿠팡 파트너스 광고 · 링크는 댓글에)"
COUPANG_HOSTS = {"link.coupang.com", "coupa.ng"}


@dataclass(frozen=True)
class Draft:
    id: str
    text: str
    scheduled_at: datetime
    reply_text: str | None = None
    affiliate: bool = False


class DraftError(ValueError):
    """A queue file that cannot be turned into a Draft."""


def parse_draft(draft_id: str, data: object) -> Draft:
    if not isinstance(data, dict):
        raise DraftError("draft must be a JSON object")
    unknown = set(data) - {"text", "scheduled_at", "reply_text", "affiliate", "coupang_url"}
    if unknown:
        raise DraftError(f"unknown fields: {', '.join(sorted(unknown))}")
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        raise DraftError("text is required")
    raw_at = data.get("scheduled_at")
    if not isinstance(raw_at, str):
        raise DraftError("scheduled_at is required (ISO 8601 with timezone)")
    try:
        scheduled_at = datetime.fromisoformat(raw_at)
    except ValueError as error:
        raise DraftError("scheduled_at is not ISO 8601") from error
    if scheduled_at.utcoffset() is None:
        raise DraftError("scheduled_at must include a timezone")
    reply_text = data.get("reply_text")
    if reply_text is not None and (not isinstance(reply_text, str) or not reply_text.strip()):
        raise DraftError("reply_text must be a non-empty string when given")
    affiliate = data.get("affiliate", False)
    if type(affiliate) is not bool:
        raise DraftError("affiliate must be true or false")
    coupang_url = data.get("coupang_url")
    if coupang_url is not None:
        text, reply_text = _with_coupang(text, reply_text, coupang_url)
        affiliate = True
    return Draft(draft_id, text, scheduled_at, reply_text, affiliate)


def _with_coupang(text: str, reply_text: str | None, url: object) -> tuple[str, str]:
    """Body gets a short ad notice; the comment gets intro + link + Coupang's required sentence."""
    if not isinstance(url, str):
        raise DraftError("coupang_url must be a string")
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in COUPANG_HOSTS or not parsed.path.strip("/"):
        raise DraftError("coupang_url must be a Coupang Partners link (https://link.coupang.com/...)")
    body = text.rstrip()
    if COUPANG_BODY_NOTICE not in body:
        body = f"{body}\n\n{COUPANG_BODY_NOTICE}"
    parts = [reply_text.strip()] if reply_text else []
    parts += [url.strip(), COUPANG_DISCLOSURE]
    return body, "\n\n".join(parts)


def load_queue(queue_dir: Path) -> tuple[list[Draft], dict[str, str]]:
    """Return valid drafts sorted by schedule, plus {draft_id: error} for invalid files."""
    drafts: list[Draft] = []
    errors: dict[str, str] = {}
    if not queue_dir.is_dir():
        return drafts, errors
    for path in sorted(queue_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            drafts.append(parse_draft(path.stem, data))
        except (DraftError, json.JSONDecodeError, UnicodeDecodeError) as error:
            errors[path.stem] = str(error)
    drafts.sort(key=lambda d: (d.scheduled_at, d.id))
    return drafts, errors
