"""Flashcards: decks per person, reviewed with spaced repetition in the feed or in flashcard-only mode.

Cards come from three places: made in the app, written by the LLM from a card in the feed, or imported.
Imports: CSV/TSV (front, back; Quizlet exports this) and Anki .apkg (AnkiWeb's shared decks), whose
"collection.anki2"/".anki21" SQLite holds notes as fields joined by \\x1f. Anki's newest export format
(collection.anki21b, zstd-compressed) isn't read: export with "Support older Anki versions" instead.
Text only for now: images and sounds in Anki notes are dropped.
"""

from __future__ import annotations

import csv
import html
import io
import re
import sqlite3
import tempfile
import zipfile
from typing import Any

MAX_CARDS = 20_000  # per import
_TAG = re.compile(r"<[^>]+>")
_SOUND = re.compile(r"\[sound:[^\]]*\]")
_BR = re.compile(r"<\s*(br|/div|/p|/li)\s*/?>", re.I)


def clean(field: str) -> str:
    """Anki/HTML field -> plain text (line breaks kept, media references dropped)."""
    t = _SOUND.sub("", field)
    t = _BR.sub("\n", t)
    t = html.unescape(_TAG.sub("", t))
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def parse_csv(data: bytes) -> list[tuple[str, str]]:
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="\t,;")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample else csv.excel
    out = []
    for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
        if len(row) < 2:
            continue
        front, back = row[0].strip(), row[1].strip()
        if i == 0 and front.casefold() in ("front", "term", "question", "word") and back:
            continue  # a header row
        if front and back:
            out.append((front[:2000], back[:4000]))
        if len(out) >= MAX_CARDS:
            break
    return out


def parse_apkg(data: bytes) -> list[tuple[str, str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        name = next((n for n in ("collection.anki21", "collection.anki2") if n in names), None)
        if name is None:
            if "collection.anki21b" in names:
                raise ValueError("this is Anki's newest format: export again with 'Support older Anki versions' ticked")
            raise ValueError("no Anki collection in that file")
        blob = z.read(name)
    with tempfile.NamedTemporaryFile(suffix=".anki2") as f:
        f.write(blob)
        f.flush()
        db = sqlite3.connect(f.name)
        try:
            rows = db.execute("SELECT flds FROM notes").fetchall()
        finally:
            db.close()
    out = []
    for (flds,) in rows:
        parts = [clean(p) for p in str(flds).split("\x1f")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            out.append((parts[0][:2000], parts[1][:4000]))
        if len(out) >= MAX_CARDS:
            break
    return out


def parse(data: bytes, filename: str) -> list[tuple[str, str]]:
    if filename.lower().endswith((".apkg", ".colpkg")) or data[:2] == b"PK":
        return parse_apkg(data)
    return parse_csv(data)


def card_for(row: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    """A flashcard as a feed card: the front, with the back revealed on demand."""
    return {
        "kind": "flashcard", "label": f"{labels.get('flashcard', 'Flashcard')} · {row['deck_name']}",
        "title": row["front"], "subtitle": "", "blurb": "", "image": "", "url": "",
        "actions": [], "embed": {}, "ref": f"fc:{row['id']}", "why": "",
        "flash": {"id": row["id"], "back": row["back"], "deck": row["deck_id"]},
    }
