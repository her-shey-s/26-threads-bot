import json
import sqlite3
from pathlib import Path

from .features import extract_features
from .models import Observation, RankedPost, Trend


SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    report_date TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    candidate_count INTEGER,
    unique_count INTEGER,
    error_code TEXT
);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    author TEXT,
    url TEXT,
    text TEXT NOT NULL,
    published_at TEXT,
    media_count INTEGER
);
CREATE TABLE IF NOT EXISTS metrics (
    post_id TEXT NOT NULL REFERENCES posts(id),
    observed_at TEXT NOT NULL,
    likes INTEGER,
    replies INTEGER,
    reposts INTEGER,
    quotes INTEGER,
    unavailable_reasons TEXT NOT NULL,
    PRIMARY KEY (post_id, observed_at)
);
CREATE TABLE IF NOT EXISTS features (
    post_id TEXT NOT NULL REFERENCES posts(id),
    observed_at TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (post_id, observed_at)
);
CREATE TABLE IF NOT EXISTS trends (
    report_date TEXT NOT NULL,
    keyword TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (report_date, keyword, source)
);
CREATE TABLE IF NOT EXISTS daily_rankings (
    report_date TEXT NOT NULL,
    kind TEXT NOT NULL,
    rank INTEGER NOT NULL,
    post_id TEXT NOT NULL REFERENCES posts(id),
    observed_at TEXT NOT NULL,
    score REAL NOT NULL,
    run_id INTEGER NOT NULL REFERENCES job_runs(id),
    PRIMARY KEY (report_date, kind, rank)
);
"""


def open_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def save_results(
    connection: sqlite3.Connection,
    run_id: int,
    report_date: str,
    trends: list[Trend],
    items: list[Observation],
    rankings: dict[str, list[RankedPost]],
) -> None:
    for trend in trends:
        connection.execute(
            "INSERT OR IGNORE INTO trends VALUES (?, ?, ?)",
            (report_date, trend.keyword, trend.source),
        )
    for item in items:
        post = item.post
        connection.execute(
            """INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET author=excluded.author, url=excluded.url,
            text=excluded.text, published_at=excluded.published_at,
            media_count=excluded.media_count""",
            (post.id, post.author, post.url, post.text,
             post.published_at.isoformat() if post.published_at else None, post.media_count),
        )
        observed_at = item.observed_at.isoformat()
        connection.execute(
            """INSERT INTO metrics VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id, observed_at) DO NOTHING""",
            (post.id, observed_at, *(item.metrics.get(name) for name in ("likes", "replies", "reposts", "quotes")),
             json.dumps(item.unavailable_reasons, ensure_ascii=False)),
        )
        connection.execute(
            "INSERT OR IGNORE INTO features VALUES (?, ?, ?)",
            (post.id, observed_at, json.dumps(extract_features(post), ensure_ascii=False)),
        )
    connection.execute("DELETE FROM daily_rankings WHERE report_date = ?", (report_date,))
    for kind, rows in rankings.items():
        connection.executemany(
            "INSERT INTO daily_rankings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(report_date, kind, index, row.observation.post.id,
              row.observation.observed_at.isoformat(), row.score, run_id)
             for index, row in enumerate(rows, 1)],
        )
