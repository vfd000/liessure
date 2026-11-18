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
    title   TEXT NOT NULL DEFAULT '',
    user    TEXT NOT NULL DEFAULT 'owner'
);
CREATE INDEX IF NOT EXISTS events_kind_ts ON events (kind, ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ideas (
    id      INTEGER PRIMARY KEY,
    ts      REAL NOT NULL,
    text    TEXT NOT NULL,
    about   TEXT NOT NULL DEFAULT '',   -- the card it was about, "kind: title", if any
    status  TEXT NOT NULL DEFAULT 'new', -- new | planned | done | dropped
    user    TEXT NOT NULL DEFAULT 'owner'
);
CREATE TABLE IF NOT EXISTS seen (          -- cards you opened or played, kept to quiz you on
    kind TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL, subtitle TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', ts REAL NOT NULL,
    asked INTEGER NOT NULL DEFAULT 0,          -- 1 once the curator has written (or declined) a question
    user TEXT NOT NULL DEFAULT 'owner',
    PRIMARY KEY (user, kind, ref)
);
CREATE TABLE IF NOT EXISTS recall (
    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL,
    question TEXT NOT NULL, choices TEXT NOT NULL, answer INTEGER NOT NULL, explain TEXT NOT NULL DEFAULT '',
    box INTEGER NOT NULL DEFAULT 0,            -- Leitner box: how many times in a row you've got it
    due REAL NOT NULL, created REAL NOT NULL, user TEXT NOT NULL DEFAULT 'owner'
);
CREATE TABLE IF NOT EXISTS recall_answers (ts REAL NOT NULL, recall_id INTEGER NOT NULL, correct INTEGER NOT NULL,
    user TEXT NOT NULL DEFAULT 'owner');
CREATE TABLE IF NOT EXISTS todo_estimates (  -- how long a to-do item takes, redone when its text changes
    item_id INTEGER PRIMARY KEY, text_hash TEXT NOT NULL, minutes INTEGER NOT NULL,
    label TEXT NOT NULL, assumption TEXT NOT NULL DEFAULT '', ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS math_attempts (   -- every math card answered: right, tries, gave up
    ts REAL NOT NULL, ref TEXT NOT NULL, topic TEXT NOT NULL, correct INTEGER NOT NULL,
    tries INTEGER NOT NULL, revealed INTEGER NOT NULL, user TEXT NOT NULL DEFAULT 'owner'
);
CREATE TABLE IF NOT EXISTS sessions (          -- signed-in browsers: sha256 of the cookie token
    token_hash TEXT PRIMARY KEY, sub TEXT NOT NULL, user TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    exp REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS math_help (       -- help used on a math problem: hint, fix (LLM), tutor (Claude)
    ts REAL NOT NULL, user TEXT NOT NULL, ref TEXT NOT NULL, topic TEXT NOT NULL, kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (         -- every card the feed showed someone, to find it again
    user TEXT NOT NULL, key TEXT NOT NULL, ts REAL NOT NULL, card TEXT NOT NULL, text TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT '',            -- '' | 'done' (swiped away, kept in a list) | 'later' (comes back)
    state_ts REAL NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0, box INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user, key)
);
CREATE INDEX IF NOT EXISTS history_user_ts ON history (user, ts);
CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY, user TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS flashcards (
    id INTEGER PRIMARY KEY, deck_id INTEGER NOT NULL, user TEXT NOT NULL, front TEXT NOT NULL, back TEXT NOT NULL,
    box INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL DEFAULT 0, created REAL NOT NULL,
    reviews INTEGER NOT NULL DEFAULT 0, lapses INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS flashcards_due ON flashcards (user, due);
CREATE TABLE IF NOT EXISTS avoid (          -- topics a reader never wants (avoid.py)
    user TEXT NOT NULL, term TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (user, term)
);
CREATE TABLE IF NOT EXISTS rejected (
    kind TEXT NOT NULL, ref TEXT NOT NULL, ts REAL NOT NULL, PRIMARY KEY (kind, ref)
);
"""

MAX_SHOWN = 3          # a pick is retired after it's been shown this many times
MAX_AGE_DAYS = 30
MAX_AGE_DAYS_BY_KIND = {"news": 2}
# Days until a recall question comes back, by box: get it right and it moves up a box.
RECALL_INTERVALS = (1, 3, 7, 21, 60, 180)


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Databases from before multi-user: add the user column (their rows are the owner's)."""
        def cols(table: str) -> set[str]:
            return {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})").fetchall()}
        hist = cols("history")
        for col, decl in (("state", "TEXT NOT NULL DEFAULT ''"), ("state_ts", "REAL NOT NULL DEFAULT 0"),
                          ("due", "REAL NOT NULL DEFAULT 0"), ("box", "INTEGER NOT NULL DEFAULT 0")):
            if col not in hist:
                self._db.execute(f"ALTER TABLE history ADD COLUMN {col} {decl}")
        for table in ("events", "ideas", "recall", "recall_answers", "math_attempts"):
            if "user" not in cols(table):
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN user TEXT NOT NULL DEFAULT 'owner'")
        if "user" not in cols("seen"):  # the primary key changes, so copy into a new table
            self._db.executescript("""
                ALTER TABLE seen RENAME TO seen_v1;
                CREATE TABLE seen (
                    kind TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL, subtitle TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', ts REAL NOT NULL,
                    asked INTEGER NOT NULL DEFAULT 0, user TEXT NOT NULL DEFAULT 'owner',
                    PRIMARY KEY (user, kind, ref));
                INSERT INTO seen (kind, ref, title, subtitle, text, url, ts, asked, user)
                    SELECT kind, ref, title, subtitle, text, url, ts, asked, 'owner' FROM seen_v1;
                DROP TABLE seen_v1;""")
        self._db.commit()

    # --- sessions
    def put_session(self, token_hash: str, sub: str, user: str, name: str, exp: float) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE exp < ?", (time.time(),))
            self._db.execute("INSERT OR REPLACE INTO sessions (token_hash, sub, user, name, exp) VALUES (?, ?, ?, ?, ?)",
                             (token_hash, sub, user, name, exp))

    def get_session(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    # --- recall: quiz questions about cards you opened, spaced out
    def remember_card(self, kind: str, ref: str, title: str, subtitle: str, text: str, url: str,
                      user: str = "owner") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO seen (kind, ref, title, subtitle, text, url, ts, user) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (user, kind, ref) DO UPDATE SET ts = excluded.ts",
                (kind, ref[:128], title[:300], subtitle[:300], text[:2000], url[:1000], time.time(), user),
            )

    def cards_to_ask(self, limit: int = 8, min_age_hours: float = 6) -> list[dict[str, Any]]:
        """Opened a while ago and not yet turned into a question, oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM seen WHERE asked = 0 AND ts <= ? ORDER BY ts LIMIT ?",
                (time.time() - min_age_hours * 3600, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_recall(self, kind: str, ref: str, question: str, choices: list[str], answer: int, explain: str,
                   user: str = "owner") -> int:
        now = time.time()
        with self._lock, self._db:
            seen = self._db.execute("SELECT ts FROM seen WHERE user = ? AND kind = ? AND ref = ?",
                                    (user, kind, ref)).fetchone()
            first = max(now, (seen["ts"] if seen else now) + RECALL_INTERVALS[0] * 86400)
            cur = self._db.execute(
                "INSERT INTO recall (kind, ref, question, choices, answer, explain, due, created, user) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, ref, question, json.dumps(choices), answer, explain, first, now, user),
            )
            self._db.execute("UPDATE seen SET asked = 1 WHERE user = ? AND kind = ? AND ref = ?", (user, kind, ref))
        return int(cur.lastrowid)

    def mark_asked(self, kind: str, ref: str, user: str = "owner") -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE seen SET asked = 1 WHERE user = ? AND kind = ? AND ref = ?", (user, kind, ref))

    def due_recall(self, user: str = "owner", snooze_hours: float = 2) -> dict[str, Any] | None:
        """The most overdue question, with the card it's about. Snoozed a little once served, so a
        refresh doesn't ask it again straight away; answering sets the real next date."""
        now = time.time()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT r.*, s.title AS card_title, s.url AS card_url, s.ts AS seen_ts FROM recall r "
                "LEFT JOIN seen s ON s.user = r.user AND s.kind = r.kind AND s.ref = r.ref "
                "WHERE r.user = ? AND r.due <= ? ORDER BY r.due LIMIT 1",
                (user, now),
            ).fetchone()
            if not row:
                return None
            self._db.execute("UPDATE recall SET due = ? WHERE id = ?", (now + snooze_hours * 3600, row["id"]))
        out = dict(row)
        out["choices"] = json.loads(out["choices"])
        return out

    def answer_recall(self, recall_id: int, correct: bool, user: str = "owner") -> dict[str, Any] | None:
        now = time.time()
        with self._lock, self._db:
            row = self._db.execute("SELECT box FROM recall WHERE id = ? AND user = ?", (recall_id, user)).fetchone()
            if not row:
                return None
            box = min(row["box"] + 1, len(RECALL_INTERVALS) - 1) if correct else 0
            days = RECALL_INTERVALS[box] if correct else RECALL_INTERVALS[0]
            self._db.execute("UPDATE recall SET box = ?, due = ? WHERE id = ?", (box, now + days * 86400, recall_id))
            self._db.execute("INSERT INTO recall_answers (ts, recall_id, correct, user) VALUES (?, ?, ?, ?)",
                             (now, recall_id, int(correct), user))
        return {"box": box, "next_in_days": days, **self.recall_stats(user)}

    def recall_stats(self, user: str = "owner", days: float = 30) -> dict[str, int]:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) n, COALESCE(SUM(correct), 0) ok FROM recall_answers WHERE user = ? AND ts >= ?",
                (user, time.time() - days * 86400),
            ).fetchone()
        return {"answered_30d": row["n"], "correct_30d": row["ok"]}

    # --- math progress
    def record_math(self, ref: str, topic: str, correct: bool, tries: int, revealed: bool, user: str = "owner") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO math_attempts (ts, ref, topic, correct, tries, revealed, user) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (time.time(), ref[:64], topic[:40], int(correct), tries, int(revealed), user))

    def record_math_help(self, ref: str, topic: str, kind: str, user: str = "owner") -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO math_help (ts, user, ref, topic, kind) VALUES (?, ?, ?, ?, ?)",
                             (time.time(), user, ref[:64], topic[:40], kind[:10]))

    def math_stats(self, now: float | None = None, user: str = "owner") -> dict[str, Any]:
        """Today, this week (from Monday), the daily streak and the weakest topic of the last 30 days.
        Days are local to the server (the CT runs on household time)."""
        import datetime as _dt
        now = now or time.time()
        today = _dt.date.fromtimestamp(now)
        midnight = _dt.datetime.combine(today, _dt.time()).timestamp()
        monday = midnight - today.weekday() * 86400
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, topic, correct, revealed FROM math_attempts WHERE user = ? AND ts >= ?",
                (user, now - 400 * 86400)
            ).fetchall()

        with self._lock:
            helps = self._db.execute(
                "SELECT ts, ref, topic, kind FROM math_help WHERE user = ? AND ts >= ?", (user, now - 400 * 86400)
            ).fetchall()

        def tally(since: float) -> dict[str, int]:
            sel = [r for r in rows if r["ts"] >= since]
            return {"tried": len(sel), "right": sum(r["correct"] for r in sel),
                    "helped": len({h["ref"] for h in helps if h["ts"] >= since})}
        solved_days = {_dt.date.fromtimestamp(r["ts"]) for r in rows if r["correct"]}
        streak, day = 0, today if today in solved_days else today - _dt.timedelta(days=1)
        while day in solved_days:
            streak += 1
            day -= _dt.timedelta(days=1)
        by_topic: dict[str, list[int]] = {}
        for r in rows:
            if r["ts"] >= now - 30 * 86400:
                by_topic.setdefault(r["topic"], []).append(r["correct"])
        rated = {t: sum(v) / len(v) for t, v in by_topic.items() if len(v) >= 3}
        weakest = min(rated, key=rated.get) if rated else ""
        help_by_topic: dict[str, int] = {}
        for h in helps:
            if h["ts"] >= now - 30 * 86400 and h["topic"]:
                help_by_topic[h["topic"]] = help_by_topic.get(h["topic"], 0) + 1
        kinds: dict[str, int] = {}
        for h in helps:
            if h["ts"] >= now - 30 * 86400:
                kinds[h["kind"]] = kinds.get(h["kind"], 0) + 1
        most_help = max(help_by_topic, key=help_by_topic.get) if help_by_topic else ""
        return {"today": tally(midnight), "week": tally(monday), "all": tally(0), "streak_days": streak,
                "weakest": weakest, "weakest_rate": round(rated[weakest], 2) if weakest else None,
                "most_help": most_help, "help_30d": kinds}

    # --- to-do estimates
    def todo_estimates(self) -> dict[int, dict[str, Any]]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM todo_estimates").fetchall()
        return {r["item_id"]: dict(r) for r in rows}

    def put_todo_estimate(self, item_id: int, text_hash: str, minutes: int, label: str, assumption: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO todo_estimates (item_id, text_hash, minutes, label, assumption, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)", (item_id, text_hash, minutes, label, assumption, time.time()))

    # --- history: cards shown, searchable
    HISTORY_MAX = 100_000  # effectively everything: search should find very old cards

    @staticmethod
    def card_key(c: dict[str, Any]) -> str:
        return f"{c.get('kind')}:{c.get('ref') or c.get('url')}"[:300]

    def remember_shown(self, cards: list[dict[str, Any]], user: str = "owner") -> None:
        now = time.time()
        rows = []
        for i, c in enumerate(cards):
            key = self.card_key(c)
            text = " ".join(str(c.get(k) or "") for k in ("title", "subtitle", "label", "blurb")).casefold()
            rows.append((user, key, now + i / 1000, json.dumps(c), text[:3000]))
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO history (user, key, ts, card, text) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (user, key) DO UPDATE SET ts = excluded.ts, card = excluded.card, text = excluded.text",
                rows)
            self._db.execute(
                "DELETE FROM history WHERE user = ? AND state = '' AND ts < (SELECT ts FROM history WHERE user = ? "
                "ORDER BY ts DESC LIMIT 1 OFFSET ?)", (user, user, self.HISTORY_MAX - 1))

    # Swiped: 'done' leaves the feed for a list; 'later' is a list too and comes back on a spaced schedule.
    LATER_DAYS = (1, 3, 7, 14, 30, 60)

    def set_state(self, card: dict[str, Any], state: str, user: str = "owner") -> None:
        now = time.time()
        key = self.card_key(card)
        text = " ".join(str(card.get(k) or "") for k in ("title", "subtitle", "label", "blurb")).casefold()
        due = now + self.LATER_DAYS[0] * 86400 if state == "later" else 0
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO history (user, key, ts, card, text, state, state_ts, due, box) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0) "
                "ON CONFLICT (user, key) DO UPDATE SET state = excluded.state, state_ts = excluded.state_ts, "
                "due = excluded.due, box = 0",
                (user, key, now, json.dumps(card), text[:3000], state, now, due))

    def state_list(self, state: str, user: str = "owner", limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT card, due FROM history WHERE user = ? AND state = ? ORDER BY state_ts DESC LIMIT ?",
                (user, state, limit)).fetchall()
        return [{**json.loads(r["card"]), "due": r["due"]} for r in rows]

    def done_keys(self, user: str = "owner") -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT key FROM history WHERE user = ? AND state = 'done'", (user,)).fetchall()
        return {r["key"] for r in rows}

    def due_later(self, user: str = "owner") -> dict[str, Any] | None:
        """The most overdue 'later' card, rescheduled further out (it stays on the Later list)."""
        now = time.time()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT key, card, box FROM history WHERE user = ? AND state = 'later' AND due <= ? ORDER BY due LIMIT 1",
                (user, now)).fetchone()
            if not row:
                return None
            box = min(row["box"] + 1, len(self.LATER_DAYS) - 1)
            self._db.execute("UPDATE history SET box = ?, due = ? WHERE user = ? AND key = ?",
                             (box, now + self.LATER_DAYS[box] * 86400, user, row["key"]))
        return json.loads(row["card"])

    def search_history(self, q: str, user: str = "owner", limit: int = 40) -> list[dict[str, Any]]:
        """Cards shown to this person, newest first, whose text has every word of q."""
        words = [w for w in q.casefold().split() if w][:8]
        where = " AND ".join("text LIKE ? ESCAPE '\\'" for _ in words) or "1"
        args = [f"%{w.replace('%', '\\%').replace('_', '\\_')}%" for w in words]
        with self._lock:
            rows = self._db.execute(
                f"SELECT card FROM history WHERE user = ? AND {where} ORDER BY ts DESC LIMIT ?",
                (user, *args, limit)).fetchall()
        return [json.loads(r["card"]) for r in rows]

    # --- flashcards (flashcards.py)
    FLASH_DAYS = (1, 3, 7, 21, 60, 180)
    AGAIN_MINUTES = 10

    def add_deck(self, name: str, user: str = "owner", source: str = "") -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO decks (user, name, source, created) VALUES (?, ?, ?, ?)",
                                   (user, name.strip()[:80] or "Deck", source[:40], time.time()))
        return int(cur.lastrowid)

    def deck_by_name(self, name: str, user: str = "owner") -> int | None:
        with self._lock:
            row = self._db.execute("SELECT id FROM decks WHERE user = ? AND name = ?", (user, name)).fetchone()
        return row["id"] if row else None

    def decks(self, user: str = "owner") -> list[dict[str, Any]]:
        now = time.time()
        with self._lock:
            rows = self._db.execute(
                "SELECT d.id, d.name, d.source, COUNT(f.id) AS cards, "
                "COALESCE(SUM(CASE WHEN f.due <= ? THEN 1 ELSE 0 END), 0) AS due "
                "FROM decks d LEFT JOIN flashcards f ON f.deck_id = d.id WHERE d.user = ? "
                "GROUP BY d.id ORDER BY d.created", (now, user)).fetchall()
        return [dict(r) for r in rows]

    def delete_deck(self, deck_id: int, user: str = "owner") -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM decks WHERE id = ? AND user = ?", (deck_id, user))
            self._db.execute("DELETE FROM flashcards WHERE deck_id = ? AND user = ?", (deck_id, user))
        return cur.rowcount == 1

    def add_flashcards(self, deck_id: int, cards: list[tuple[str, str]], user: str = "owner") -> int:
        now = time.time()
        with self._lock, self._db:
            if not self._db.execute("SELECT 1 FROM decks WHERE id = ? AND user = ?", (deck_id, user)).fetchone():
                raise KeyError("no such deck")
            # New cards are due now, a little apart so they come in the order they were given.
            self._db.executemany(
                "INSERT INTO flashcards (deck_id, user, front, back, due, created) VALUES (?, ?, ?, ?, ?, ?)",
                [(deck_id, user, f, b, now + i / 1000, now) for i, (f, b) in enumerate(cards)])
        return len(cards)

    def deck_cards(self, deck_id: int, user: str = "owner", limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, front, back, box, due FROM flashcards WHERE deck_id = ? AND user = ? ORDER BY id LIMIT ?",
                (deck_id, user, limit)).fetchall()
        return [dict(r) for r in rows]

    def delete_flashcard(self, card_id: int, user: str = "owner") -> bool:
        with self._lock, self._db:
            cur = self._db.execute("DELETE FROM flashcards WHERE id = ? AND user = ?", (card_id, user))
        return cur.rowcount == 1

    def next_flashcard(self, user: str = "owner", decks: list[int] | None = None,
                       skip: set[int] | None = None) -> dict[str, Any] | None:
        """The most overdue card (from these decks, if given)."""
        where, args = "f.user = ? AND f.due <= ?", [user, time.time()]
        if decks:
            where += f" AND f.deck_id IN ({','.join('?' * len(decks))})"
            args += decks
        if skip:
            where += f" AND f.id NOT IN ({','.join('?' * len(skip))})"
            args += list(skip)
        with self._lock:
            row = self._db.execute(
                f"SELECT f.id, f.front, f.back, f.deck_id, d.name AS deck_name FROM flashcards f "
                f"JOIN decks d ON d.id = f.deck_id WHERE {where} ORDER BY f.due LIMIT 1", args).fetchone()
        return dict(row) if row else None

    def grade_flashcard(self, card_id: int, good: bool, user: str = "owner") -> dict[str, Any] | None:
        now = time.time()
        with self._lock, self._db:
            row = self._db.execute("SELECT box FROM flashcards WHERE id = ? AND user = ?", (card_id, user)).fetchone()
            if not row:
                return None
            if good:  # up a box: 1, 3, 7, 21, 60, then 180 days
                box = min(row["box"] + 1, len(self.FLASH_DAYS))
                due = now + self.FLASH_DAYS[box - 1] * 86400
            else:
                box, due = 0, now + self.AGAIN_MINUTES * 60
            self._db.execute(
                "UPDATE flashcards SET box = ?, due = ?, reviews = reviews + 1, lapses = lapses + ? WHERE id = ?",
                (box, due, 0 if good else 1, card_id))
        return {"box": box, "due": due}

    # --- disinterests
    def avoid_list(self, user: str = "owner") -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT term FROM avoid WHERE user = ? ORDER BY ts", (user,)).fetchall()
        return [r["term"] for r in rows]

    def add_avoid(self, term: str, user: str = "owner") -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR IGNORE INTO avoid (user, term, ts) VALUES (?, ?, ?)",
                             (user, term.strip().casefold()[:60], time.time()))

    def remove_avoid(self, term: str, user: str = "owner") -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM avoid WHERE user = ? AND term = ?", (user, term.strip().casefold()))

    # --- ideas: improvements jotted down in the app, to build later
    def add_idea(self, text: str, about: str = "", user: str = "owner") -> int:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO ideas (ts, text, about, user) VALUES (?, ?, ?, ?)",
                                   (time.time(), text, about, user))
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
    def record(self, kind: str, ref: str, action: str, title: str = "", user: str = "owner") -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events (ts, kind, ref, action, title, user) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), kind, ref[:128], action[:16], title[:300], user),
            )

    def recent_refs(self, kind: str, limit: int = 10) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ref, MAX(ts) t FROM events WHERE user = 'owner' AND kind = ? AND ref != '' "
                "GROUP BY ref ORDER BY t DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [r["ref"] for r in rows]

    def recent_titles(self, kind: str, limit: int = 15) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT title, MAX(ts) t FROM events WHERE user = 'owner' AND kind = ? AND title != '' "
                "GROUP BY title ORDER BY t DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        return [r["title"] for r in rows]

    def events_since(self, days: float) -> list[tuple[str, str]]:
        """(kind, title) of distinct things opened or played, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT kind, title, MAX(ts) t FROM events WHERE user = 'owner' AND ts >= ? AND title != '' "
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

    def drop(self, kind: str, ref: str) -> None:
        """Take a pick out of the pool for good (it stays known, so it isn't picked again)."""
        with self._lock, self._db:
            self._db.execute("DELETE FROM curated WHERE kind = ? AND ref = ?", (kind, ref))
            self._db.execute("INSERT OR IGNORE INTO rejected (kind, ref, ts) VALUES (?, ?, ?)", (kind, ref, time.time()))

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
            # Phrase cards from the retired "cheers!" topic (toasts are about drinking).
            self._db.execute("DELETE FROM curated WHERE kind = 'language' AND ref LIKE 'phrase:%:cheers!'")
            for kind, days in MAX_AGE_DAYS_BY_KIND.items():
                self._db.execute("DELETE FROM curated WHERE kind = ? AND added_at < ?", (kind, now - days * 86400))
            # Remember rejections longer, so the same dud isn't re-scored every run.
            self._db.execute("DELETE FROM rejected WHERE ts < ?", (time.time() - 90 * 86400,))
