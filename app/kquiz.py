"""K-drama quiz: a photo of a well-known South Korean actor, and who is it, or how old are they?

People come from Wikidata (actors with South Korean citizenship, a photo, a birth date and enough
Wikipedia articles to be well known), fetched once a day in the background so a slow query never
holds up the feed. Photos are Wikimedia Commons'. For readers learning Korean.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx

from .sources import Card, Unavailable

log = logging.getLogger("liessure.kquiz")

QUERY = """
SELECT ?p ?name ?ko ?img ?dob ?gender WHERE {
  VALUES ?occ { wd:Q33999 wd:Q10798782 }
  ?p wdt:P106 ?occ; wdt:P27 wd:Q884; wdt:P18 ?img; wdt:P569 ?dob; wdt:P21 ?gender;
     wikibase:sitelinks ?links.
  FILTER(?links >= 20)
  OPTIONAL { ?p rdfs:label ?ko FILTER(lang(?ko) = "ko") }
  OPTIONAL { ?p rdfs:label ?name FILTER(lang(?name) = "en") }
}
LIMIT 1500
"""
TTL = 24 * 3600

_people: list[dict[str, Any]] = []
_fetched = 0.0
_task: asyncio.Task | None = None

TEXT = {
    "es": {"who": "¿Quién es?", "age": "¿Cuántos años tiene?", "label": "Quiz de K-drama",
           "is": "Es {name} ({ko}), nació el {dob}: tiene {age} años.", "open": "Más información"},
    "en": {"who": "Who is this?", "age": "How old are they?", "label": "K-drama quiz",
           "is": "It's {name} ({ko}), born {dob}: {age} years old.", "open": "More info"},
}


async def _fetch(client: httpx.AsyncClient) -> None:
    global _people, _fetched
    r = await client.get("https://query.wikidata.org/sparql", params={"query": QUERY, "format": "json"},
                         headers={"Accept": "application/sparql-results+json"}, timeout=30)
    r.raise_for_status()
    seen: dict[str, dict[str, Any]] = {}
    for b in r.json()["results"]["bindings"]:
        qid = b["p"]["value"].rsplit("/", 1)[-1]
        name = (b.get("name") or {}).get("value") or ""
        if qid in seen or not name or not b["dob"]["value"][:4].isdigit():
            continue
        img = b["img"]["value"].replace("http://", "https://", 1)
        seen[qid] = {"qid": qid, "name": name, "ko": (b.get("ko") or {}).get("value", ""),
                     "img": img + "?width=640", "dob": date.fromisoformat(b["dob"]["value"][:10]),
                     "gender": b["gender"]["value"].rsplit("/", 1)[-1]}
    if len(seen) >= 8:
        _people, _fetched = list(seen.values()), time.time()


async def people(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """The list, refreshed daily in the background; the first time it waits a little for it."""
    global _task
    if (not _people or time.time() - _fetched > TTL) and (_task is None or _task.done()):
        _task = asyncio.create_task(_fetch(client))
    if not _people and _task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(_task), 6)
        except asyncio.TimeoutError:
            raise Unavailable("still fetching the K-drama actors")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise Unavailable(f"couldn't fetch the K-drama actors: {e}")
    if not _people:
        raise Unavailable("no K-drama actors")
    return _people


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


async def card(client: httpx.AsyncClient, lang: str, wiki: str, rng: random.Random,
               today: date | None = None) -> Card:
    everyone = await people(client)
    today = today or date.today()
    t = TEXT.get(lang, TEXT["en"])
    p = rng.choice(everyone)
    age = _age(p["dob"], today)
    if rng.random() < 0.5:
        same = [x["name"] for x in everyone if x["gender"] == p["gender"] and x["name"] != p["name"]]
        choices = rng.sample(same, min(3, len(same))) + [p["name"]]
        question, right = t["who"], p["name"]
    else:
        offsets = rng.sample([-9, -6, -4, -3, 3, 4, 6, 9], 3)
        choices = [str(age + o) for o in offsets if age + o > 12][:3] + [str(age)]
        question, right = t["age"], str(age)
    rng.shuffle(choices)
    solution = t["is"].format(name=p["name"], ko=p["ko"] or p["name"], dob=p["dob"].isoformat(), age=age)
    return Card(
        kind="kquiz", label=t["label"], title=question, image=p["img"],
        url=f"https://{wiki}.m.wikipedia.org/wiki/Special:Search?search={quote(p['name'])}",
        open_label=t["open"],
        quiz={"choices": choices, "answer": choices.index(right), "solution": solution},
        ref=f"kquiz:{p['qid']}:{'who' if question == t['who'] else 'age'}",
    )
