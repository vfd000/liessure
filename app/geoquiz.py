"""Geography quiz: capitals, flags, continents, and which country is bigger or more populous.

Countries come from Wikidata (sovereign states that still exist, with a capital, a continent, a population
and a flag), with their names in the reader's language, fetched once a week in the background so a slow
query never holds up the feed. Answers are Wikidata's, never a model's. Flags are Wikimedia Commons'.

Left out on purpose: neighbours (Wikidata's "shares border with" counts sea borders, so Japan "borders"
the United States), the capital of a country with more than one, and the continent of one that spans two.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any
from urllib.parse import quote

import httpx

from .sources import Card, Unavailable

log = logging.getLogger("liessure.geoquiz")

QUERY = """
SELECT ?c (SAMPLE(?es) AS ?nameEs) (SAMPLE(?en) AS ?nameEn) (SAMPLE(?capEs) AS ?capitalEs)
       (SAMPLE(?capEn) AS ?capitalEn) (COUNT(DISTINCT ?cap) AS ?caps) (SAMPLE(?contEs) AS ?continentEs)
       (SAMPLE(?contEn) AS ?continentEn) (COUNT(DISTINCT ?cont) AS ?conts) (MAX(?pop) AS ?population)
       (MAX(?area) AS ?areaM2) (SAMPLE(?flag) AS ?flagImg)
WHERE {
  ?c wdt:P31 wd:Q3624078 .
  FILTER NOT EXISTS { ?c wdt:P576 [] }
  ?c wdt:P36 ?cap; wdt:P30 ?cont; wdt:P1082 ?pop; wdt:P41 ?flag .
  OPTIONAL { ?c p:P2046/psn:P2046/wikibase:quantityAmount ?area }
  ?c rdfs:label ?es FILTER(lang(?es) = "es")
  ?c rdfs:label ?en FILTER(lang(?en) = "en")
  OPTIONAL { ?cap rdfs:label ?capEs FILTER(lang(?capEs) = "es") }
  OPTIONAL { ?cap rdfs:label ?capEn FILTER(lang(?capEn) = "en") }
  OPTIONAL { ?cont rdfs:label ?contEs FILTER(lang(?contEs) = "es") }
  OPTIONAL { ?cont rdfs:label ?contEn FILTER(lang(?contEn) = "en") }
}
GROUP BY ?c
"""
TTL = 7 * 24 * 3600
LANGS = ("es", "en")

TEXT = {
    "es": {
        "label": "Geografía · quiz",
        "capital": "¿Cuál es la capital de {country}?",
        "capital_of": "¿De qué país es capital {capital}?",
        "flag": "¿De qué país es esta bandera?",
        "continent": "¿En qué continente está {country}?",
        "bigger": "¿Cuál de estos países es más grande?",
        "people": "¿Cuál de estos países tiene más habitantes?",
        "is_capital": "{capital} es la capital de {country} ({continent}).",
        "is_flag": "Es la bandera de {country}. Su capital es {capital}.",
        "is_continent": "{country} está en {continent}. Su capital es {capital}.",
        "is_bigger": "{country} es el más grande: {list}.",
        "is_people": "{country} tiene más habitantes: {list}.",
        "km2": "{n} km²", "inhabitants": "{n} habitantes", "open": "Wikipedia",
    },
    "en": {
        "label": "Geography quiz",
        "capital": "What is the capital of {country}?",
        "capital_of": "{capital} is the capital of which country?",
        "flag": "Whose flag is this?",
        "continent": "Which continent is {country} in?",
        "bigger": "Which of these countries is the biggest?",
        "people": "Which of these countries has the most people?",
        "is_capital": "{capital} is the capital of {country} ({continent}).",
        "is_flag": "It's the flag of {country}. Its capital is {capital}.",
        "is_continent": "{country} is in {continent}. Its capital is {capital}.",
        "is_bigger": "{country} is the biggest: {list}.",
        "is_people": "{country} has the most people: {list}.",
        "km2": "{n} km²", "inhabitants": "{n} people", "open": "Wikipedia",
    },
}

_countries: list[dict[str, Any]] = []
_fetched = 0.0
_task: asyncio.Task | None = None


def _v(b: dict[str, Any], key: str) -> str:
    return (b.get(key) or {}).get("value", "")


def parse(bindings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for b in bindings:
        names = {"es": _v(b, "nameEs"), "en": _v(b, "nameEn")}
        if not all(names.values()) or names["en"].startswith("Q"):
            continue
        try:
            pop = int(float(_v(b, "population")))
            area = float(_v(b, "areaM2")) / 1e6 if _v(b, "areaM2") else 0.0
            caps, conts = int(_v(b, "caps") or 0), int(_v(b, "conts") or 0)
        except ValueError:
            continue
        flag = _v(b, "flagImg").replace("http://", "https://", 1)
        out.append({
            "qid": _v(b, "c").rsplit("/", 1)[-1], "name": names,
            "capital": {"es": _v(b, "capitalEs") or _v(b, "capitalEn"), "en": _v(b, "capitalEn") or _v(b, "capitalEs")},
            "continent": {"es": _v(b, "continentEs") or _v(b, "continentEn"), "en": _v(b, "continentEn")},
            "one_capital": caps == 1, "one_continent": conts == 1,
            "population": pop, "area": area, "flag": flag + "?width=640" if flag else "",
        })
    return out


async def _fetch(client: httpx.AsyncClient) -> None:
    global _countries, _fetched
    r = await client.get("https://query.wikidata.org/sparql", params={"query": QUERY, "format": "json"},
                         headers={"Accept": "application/sparql-results+json"}, timeout=60)
    r.raise_for_status()
    found = parse(r.json()["results"]["bindings"])
    if len(found) >= 100:
        _countries, _fetched = found, time.time()


async def countries(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """The list, refreshed weekly in the background; the first time it waits a little for it."""
    global _task
    if (not _countries or time.time() - _fetched > TTL) and (_task is None or _task.done()):
        _task = asyncio.create_task(_fetch(client))
    if not _countries and _task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(_task), 8)
        except asyncio.TimeoutError:
            raise Unavailable("still fetching the countries")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            raise Unavailable(f"couldn't fetch the countries: {e}")
    if not _countries:
        raise Unavailable("no countries")
    return _countries


def _num(n: float, lang: str) -> str:
    s = f"{round(n):,}"
    return s.replace(",", ".") if lang == "es" else s


def _others(pool: list[dict[str, Any]], c: dict[str, Any], rng: random.Random, k: int) -> list[dict[str, Any]]:
    """k other countries, from the same continent when it has enough (a fairer guess)."""
    near = [x for x in pool if x is not c and x["continent"]["en"] == c["continent"]["en"]]
    rest = near if len(near) >= k else [x for x in pool if x is not c]
    return rng.sample(rest, k)


def card_for(everyone: list[dict[str, Any]], lang: str, rng: random.Random) -> Card:
    lang = lang if lang in LANGS else "en"
    t = TEXT[lang]
    kinds = ["capital", "capital_of", "flag", "continent", "bigger", "people"]
    kind = rng.choice(kinds)
    c = rng.choice(everyone)
    if kind in ("capital", "capital_of") and not c["one_capital"]:
        kind = "flag"
    if kind == "continent" and not c["one_continent"]:
        kind = "flag"
    if kind == "flag" and not c["flag"]:
        kind = "people"
    name, capital, continent = c["name"][lang], c["capital"][lang], c["continent"][lang]
    image, right = "", ""
    if kind in ("capital", "capital_of", "flag"):
        pool = [x for x in everyone if x["one_capital"] or kind == "flag"]
        others = _others(pool, c, rng, 3)
        if kind == "capital":
            title, image = t["capital"].format(country=name), c["flag"]
            choices, right = [x["capital"][lang] for x in others] + [capital], capital
        else:
            title = t["capital_of"].format(capital=capital) if kind == "capital_of" else t["flag"]
            image = c["flag"] if kind == "flag" else ""
            choices, right = [x["name"][lang] for x in others] + [name], name
        solution = (t["is_flag"] if kind == "flag" else t["is_capital"]).format(
            capital=capital, country=name, continent=continent)
    elif kind == "continent":
        title, image = t["continent"].format(country=name), c["flag"]
        conts = sorted({x["continent"][lang] for x in everyone if x["one_continent"] and x["continent"][lang]} - {continent})
        choices, right = rng.sample(conts, min(3, len(conts))) + [continent], continent
        solution = t["is_continent"].format(country=name, continent=continent, capital=capital)
    else:
        field = "area" if kind == "bigger" else "population"
        # Far enough apart to be a fair question: each at least 1.4 times the next.
        cands = sorted((x for x in everyone if x[field] > 0), key=lambda x: x[field])
        trio: list[dict[str, Any]] = []
        for _ in range(40):
            trio = rng.sample(cands, 3)
            v = sorted(x[field] for x in trio)
            if v[1] >= 1.4 * v[0] and v[2] >= 1.4 * v[1]:
                break
        best = max(trio, key=lambda x: x[field])
        unit = t["km2"] if field == "area" else t["inhabitants"]
        listing = ", ".join(f"{x['name'][lang]} {unit.format(n=_num(x[field], lang))}"
                            for x in sorted(trio, key=lambda x: -x[field]))
        title = t[kind]
        choices, right, c = [x["name"][lang] for x in trio], best["name"][lang], best
        solution = t["is_" + kind].format(country=right, list=listing)
    choices = list(dict.fromkeys(choices))
    rng.shuffle(choices)
    return Card(
        kind="geoquiz", label=t["label"], title=title, image=image,
        url=f"https://{lang}.m.wikipedia.org/wiki/{quote(c['name'][lang].replace(' ', '_'))}",
        open_label=t["open"],
        quiz={"choices": choices, "answer": choices.index(right), "solution": solution},
        ref=f"geoquiz:{c['qid']}:{kind}",
    )


async def card(client: httpx.AsyncClient, lang: str, rng: random.Random) -> Card:
    return card_for(await countries(client), lang, rng)
