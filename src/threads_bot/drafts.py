"""Publishing queue: one JSON file per draft under the queue directory."""

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path


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
    unknown = set(data) - {"text", "scheduled_at", "reply_text", "affiliate"}
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
    return Draft(draft_id, text, scheduled_at, reply_text, affiliate)


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
