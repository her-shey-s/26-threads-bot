import re

from .models import Post


def extract_features(post: Post) -> dict:
    """Observable text properties, not an LLM style or affiliate classification."""
    lines = [line.strip() for line in post.text.splitlines() if line.strip()]
    links = re.findall(r"https?://[^\s<>]+", post.text)
    return {
        "character_count": len(post.text),
        "line_count": len(lines),
        "first_line": lines[0] if lines else "",
        "contains_question": "?" in post.text or "？" in post.text,
        "body_links": links,
        "media_count": post.media_count,
        "affiliate_status": "unknown",
        "reply_link_status": "not_collected",
    }
