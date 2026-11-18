"""SQLite: the curated pool and what you actually opened.

Small and single-process, so plain sqlite3 with a lock is enough; calls are
quick and run in the event loop's thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS curated (
    kind      TEXT NOT NULL,
    ref       TEXT NOT NULL,
    card      TEXT NOT NULL,          -- the Card, as JSON, ready to serve
    reason    TEXT NOT NULL DEFAULT '',
    score     INTEGER NOT NULL DEFAULT 0,
    source    TEXT NOT NULL DEFAULT '',
    added_at  REAL NOT NULL,
    shown     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, ref)
);
CREATE TABLE IF NOT EXISTS events (
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    ref     TEXT NOT NULL DEFAULT '',
    action  TEXT NOT NULL,
    title   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events (kind, ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ideas (
    id      INTEGER PRIMARY KEY,
    ts      REAL NOT NULL,
    text    TEXT NOT NULL,
    about   TEXT NOT NULL DEFAULT '',   -- the card it was about, "kind: title", if any
    status  TEXT NOT NULL DEFAULT 'new' -- new | planned | done | dropped
);
CREATE TABLE IF NOT EXISTS rejected (
    kind TEXT NOT NULL, ref TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (kind, ref)
);
"""

MAX_SHOWN = 3          # a pick is retired after it's been shown this many times
MAX_AGE_DAYS = 30
MAX_AGE_DAYS_BY_KIND = {"news": 2}


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    # --- ideas: improvements jotted down in the app, to build later
    def add_idea(self, text: str, about: str = "") -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO ideas (ts, text, about) VALUES (?, ?, ?)",
                                   (time.time(), text, about))
        return int(cur.lastrowid)

    def ideas(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM ideas ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_idea_status(self, idea_id: int, status: str) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("UPDATE ideas SET status = ? WHERE id = ?", (status, idea_id))
        return cur.rowcount == 1

    # --- events
    def record(self, kind: str, ref: str, action: str, title: str = "") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events (ts, kind, ref, action, title) VALUES (?, ?, ?, ?, ?)",
                (time.time(), kind, ref[:128], action[:16], title[:300]),
            )

    def recent_refs(self, kind: str, limit: int = 10) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ref, MAX(ts) t FROM events WHERE kind = ? AND ref != '' "
                "GROUP BY ref ORDER BY t DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [r["ref"] for r in rows]

    def recent_titles(self, kind: str, limit: int = 15) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT title, MAX(ts) t FROM events WHERE kind = ? AND title != '' "
                "GROUP BY title ORDER BY t DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [r["title"] for r in rows]

    def events_since(self, days: float) -> list[tuple[str, str]]:
        """(kind, title) of distinct things opened or played, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, title, MAX(ts) t FROM events WHERE ts >= ? AND title != '' "
                "GROUP BY kind, title ORDER BY t DESC",
                (time.time() - days * 86400,),
            ).fetchall()
        return [(r["kind"], r["title"]) for r in rows]

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    # --- curated pool
    def known(self, kind: str) -> set[str]:
        with self._lock:
            a = self._db.execute("SELECT ref FROM curated WHERE kind = ?", (kind,)).fetchall()
            b = self._db.execute("SELECT ref FROM rejected WHERE kind = ?", (kind,)).fetchall()
            c = self._db.execute("SELECT DISTINCT ref FROM events WHERE kind = ?", (kind,)).fetchall()
        return {r["ref"] for r in (*a, *b, *c)}

    def add_picks(self, kind: str, picks: list[dict[str, Any]], rejected: list[str]) -> None:
        """picks: [{ref, card (dict), reason, score, source}]"""
        now = time.time()
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO curated (kind, ref, card, reason, score, source, added_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(kind, p["ref"], json.dumps(p["card"]), p.get("reason", ""), int(p.get("score") or 0),
                  p.get("source", ""), now) for p in picks],
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO rejected (kind, ref, ts) VALUES (?, ?, ?)",
                [(kind, r, now) for r in rejected],
            )

    def pool_size(self, kind: str) -> int:
        with self._lock:
            return self._db.execute(
                "SELECT COUNT(*) FROM curated WHERE kind = ? AND shown < ?", (kind, MAX_SHOWN)
            ).fetchone()[0]

    def take(self, kind: str, rng_value: float) -> dict[str, Any] | None:
        """A least-shown pick, best score first among those; marks it shown."""
        with self._lock, self._db:
            rows = self._db.execute(
                "SELECT * FROM curated WHERE kind = ? AND shown < ? ORDER BY shown, score DESC LIMIT 8",
                (kind, MAX_SHOWN),
            ).fetchall()
            if not rows:
                return None
            row = rows[int(rng_value * len(rows)) % len(rows)]
            self._db.execute("UPDATE curated SET shown = shown + 1 WHERE kind = ? AND ref = ?", (kind, row["ref"]))
            out = dict(row)
            out["card"] = json.loads(out["card"])
            return out

    def prune(self) -> None:
        now = time.time()
        with self._lock, self._db:
            self._db.execute("DELETE FROM curated WHERE added_at < ?", (now - MAX_AGE_DAYS * 86400,))
            for kind, days in MAX_AGE_DAYS_BY_KIND.items():
                self._db.execute("DELETE FROM curated WHERE kind = ? AND added_at < ?", (kind, now - days * 86400))
            # Remember rejections longer, so the same dud isn't re-scored every run.
            self._db.execute("DELETE FROM rejected WHERE ts < ?", (time.time() - 90 * 86400,))
