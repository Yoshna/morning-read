from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dateutil import parser as dateutil_parser

DB_PATH = Path(__file__).parent / "articles.db"


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id          TEXT PRIMARY KEY,
                source      TEXT NOT NULL,
                title       TEXT NOT NULL,
                url         TEXT NOT NULL,
                summary     TEXT,
                published_date TEXT,
                score       REAL DEFAULT 0,
                is_paywall  INTEGER DEFAULT 0,
                fetched_at  TEXT NOT NULL,
                ai_summary  TEXT DEFAULT ''
            )
        """)
        # migrate existing DB that lacks the column
        try:
            conn.execute("ALTER TABLE articles ADD COLUMN ai_summary TEXT DEFAULT ''")
        except Exception:
            pass
        conn.commit()


def save_articles(articles: list[dict]):
    with _get_conn() as conn:
        for a in articles:
            conn.execute(
                """INSERT OR REPLACE INTO articles
                   (id, source, title, url, summary, published_date, score, is_paywall, fetched_at, ai_summary)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    a["id"], a["source"], a["title"], a["url"],
                    a.get("summary", ""), a.get("published_date", ""),
                    a.get("score", 0), int(a.get("is_paywall", False)),
                    datetime.utcnow().isoformat(),
                    a.get("ai_summary", ""),
                ),
            )
        conn.commit()


def get_articles(days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM articles
               WHERE published_date = '' OR published_date IS NULL OR published_date >= ?
               ORDER BY score DESC""",
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def cache_age_hours() -> float:
    """Hours since last fetch; inf if never fetched."""
    with _get_conn() as conn:
        row = conn.execute("SELECT MAX(fetched_at) FROM articles").fetchone()
    if row and row[0]:
        last = dateutil_parser.parse(row[0])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds() / 3600
    return float("inf")


_init_db()
