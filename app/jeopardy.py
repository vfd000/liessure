"""Jeopardy! trivia: one clue from the show's 40-odd seasons, and what is the response?

Clues come from jwolle1/jeopardy_clue_dataset (about 554,000 clues, 1984 on, with clues that need a
picture or sound filtered out). It has no licence, so it isn't in this repo: the server downloads it
once to its data volume in the background and picks a clue by seeking to a random spot in the file.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from .sources import Card, Unavailable

log = logging.getLogger("liessure.jeopardy")

DATA_URL = "https://raw.githubusercontent.com/jwolle1/jeopardy_clue_dataset/main/combined_season1-42.tsv"
FILE = "jeopardy.tsv"
RETRY_AFTER = 3600  # a failed download is tried again an hour later, not on every card
# Clues that still lean on something you can't see or hear on a card.
NEEDS_MEDIA = re.compile(r"seen here|shown here|heard here|pictured|this video|clue crew|<a |\[", re.I)

_task: asyncio.Task | None = None
_failed_at = 0.0


def path(data_dir: str) -> Path:
    return Path(data_dir) / FILE


async def download(client: httpx.AsyncClient, data_dir: str) -> None:
    global _failed_at
    dest = path(data_dir)
    tmp = dest.with_suffix(".part")
    try:
        async with client.stream("GET", DATA_URL, timeout=httpx.Timeout(30, read=120)) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                async for chunk in r.aiter_bytes(1 << 16):
                    f.write(chunk)
        if tmp.stat().st_size < 1_000_000:
            raise ValueError(f"only {tmp.stat().st_size} bytes")
        os.replace(tmp, dest)
        log.info("jeopardy clues downloaded: %d MB", dest.stat().st_size >> 20)
    except (httpx.HTTPError, OSError, ValueError) as e:
        _failed_at = time.monotonic()
        log.warning("jeopardy clues download failed: %s", e)
        tmp.unlink(missing_ok=True)


def _ensure(client: httpx.AsyncClient, data_dir: str) -> None:
    global _task
    if (_task and not _task.done()) or (_failed_at and time.monotonic() - _failed_at < RETRY_AFTER):
        return
    _task = asyncio.create_task(download(client, data_dir))


def _clean(text: str) -> str:
    return " ".join(text.replace('\\"', '"').replace("\\'", "'").split())


def parse(line: str) -> dict | None:
    """One TSV row (round, value, daily double, category, comments, clue, response, air date, notes)."""
    cols = line.rstrip("\n").split("\t")
    if len(cols) < 8 or cols[0] == "round":
        return None
    clue, response = _clean(cols[5]), _clean(cols[6])
    if not clue or not response or NEEDS_MEDIA.search(clue):
        return None
    return {"round": cols[0], "value": cols[1] if cols[1].isdigit() else "", "category": _clean(cols[3]),
            "comments": _clean(cols[4]), "clue": clue, "response": response, "aired": cols[7][:10]}


def pick(file: Path, rng: random.Random, tries: int = 20) -> dict:
    """A random clue: seek somewhere in the file, skip to the next whole line."""
    size = file.stat().st_size
    with open(file, "rb") as f:
        for _ in range(tries):
            f.seek(rng.randrange(size))
            f.readline()
            line = f.readline()
            row = parse(line.decode("utf-8", "replace")) if line else None  # past the last line: try again
            if row:
                return row
    raise Unavailable("no usable Jeopardy clue found")


def card_for(row: dict) -> Card:
    if row["round"] == "3":
        where = "Final Jeopardy!"
    else:
        where = f"${row['value']}" if row["value"] else ("Double Jeopardy!" if row["round"] == "2" else "Jeopardy!")
    year = row["aired"][:4]
    return Card(
        kind="trivia",
        label=f"Jeopardy! · {row['category']} · {where}",
        title=row["clue"],
        subtitle=" · ".join(x for x in (row["category"], f"aired {row['aired']}" if row["aired"] else "") if x),
        blurb=row["comments"],
        # Open looks the episode up; it isn't shown as the main button while the quiz is unanswered.
        url="https://duckduckgo.com/?" + urlencode({"q": f'Jeopardy "{row["category"]}" {year}'}),
        open_label="Look it up",
        quiz={"text": True, "answer": row["response"]},
        ref="trivia:" + hashlib.sha1(f"{row['clue']}|{row['response']}".encode()).hexdigest()[:12],
    )


async def card(client: httpx.AsyncClient, data_dir: str, rng: random.Random) -> Card:
    file = path(data_dir)
    if not file.exists():
        _ensure(client, data_dir)
        raise Unavailable("Jeopardy clues are still downloading")
    return card_for(await asyncio.to_thread(pick, file, rng))
