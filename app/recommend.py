"""Recommendations: movies and series like ones in the library, and a Get it button (Jellyseerr).

Seeds are a few random movies and series from Jellyfin (their TMDB ids); Jellyseerr's "recommended" lists for
them are the candidates, minus anything already in the library or requested. Get it files a Jellyseerr
request, which hands the title to Radarr or Sonarr. Books come later (Bookshelf).
"""

from __future__ import annotations

import random
from typing import Any

import httpx

from .config import Settings
from .sources import Card, Unavailable, _TTLCache, _jellyfin_headers

TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"
_cache = _TTLCache(6 * 3600)  # candidates per seed, so a feed load doesn't hit Jellyseerr every time
# Jellyseerr mediaInfo.status: 1 unknown, 2 pending, 3 processing, 4 partially available, 5 available
HAVE = {2, 3, 4, 5}


def _headers(s: Settings) -> dict[str, str]:
    return {"X-Api-Key": s.jellyseerr_api_key}


async def seeds(client: httpx.AsyncClient, s: Settings, rng: random.Random, n: int = 3) -> list[dict[str, Any]]:
    """Random movies and series in the library that have a TMDB id: [{"type": "movie"|"tv", "tmdb", "title"}]."""
    params = {"IncludeItemTypes": "Movie,Series", "Recursive": "true", "Fields": "ProviderIds",
              "SortBy": "Random", "Limit": "40"}
    if s.jellyfin_user_id:
        params["userId"] = s.jellyfin_user_id
    r = await client.get(f"{s.jellyfin.url}/Items", params=params, headers=_jellyfin_headers(s))
    r.raise_for_status()
    out = []
    for it in r.json().get("Items") or []:
        tmdb = (it.get("ProviderIds") or {}).get("Tmdb")
        if tmdb and str(tmdb).isdigit():
            out.append({"type": "movie" if it.get("Type") == "Movie" else "tv", "tmdb": int(tmdb),
                        "title": it.get("Name") or ""})
    rng.shuffle(out)
    return out[:n]


async def candidates(client: httpx.AsyncClient, s: Settings, seed: dict[str, Any]) -> list[dict[str, Any]]:
    key = (seed["type"], seed["tmdb"])
    hit = _cache.get(key)
    if hit is None:
        r = await client.get(f"{s.jellyseerr_url}/api/v1/{seed['type']}/{seed['tmdb']}/recommendations",
                             params={"page": 1}, headers=_headers(s), timeout=15)
        r.raise_for_status()
        hit = [x for x in r.json().get("results") or []
               if (x.get("mediaInfo") or {}).get("status") not in HAVE and x.get("id")]
        _cache.put(key, hit)
    return hit


def card_for(s: Settings, seed: dict[str, Any], x: dict[str, Any]) -> Card:
    media = x.get("mediaType") or seed["type"]
    title = x.get("title") or x.get("name") or "Something"
    year = (x.get("releaseDate") or x.get("firstAirDate") or "")[:4]
    rating = x.get("voteAverage")
    sub = " · ".join(p for p in ("Movie" if media == "movie" else "Series", year,
                                 f"★ {rating:.1f}" if isinstance(rating, (int, float)) and rating else "") if p)
    return Card(
        kind="recommend",
        label=f"Because you have {seed['title']}"[:120],
        title=title + (f" ({year})" if year else ""),
        subtitle=sub,
        blurb=(x.get("overview") or "")[:600],
        image=TMDB_IMAGE + x["posterPath"] if x.get("posterPath") else "",
        url=f"{s.jellyseerr_link or s.jellyseerr_url}/{media}/{x['id']}",
        open_label="Details",
        request={"media": media, "tmdb": int(x["id"]), "title": title},
        ref=f"rec:{media}:{x['id']}",
    )


async def card(client: httpx.AsyncClient, s: Settings, rng: random.Random, seen: set[str]) -> Card:
    if not (s.jellyseerr_url and s.jellyseerr_api_key and s.jellyfin.url and s.jellyfin_token):
        raise Unavailable("jellyseerr not configured")
    for seed in await seeds(client, s, rng):
        pool = [x for x in await candidates(client, s, seed) if f"rec:{x.get('mediaType') or seed['type']}:{x['id']}" not in seen]
        if pool:
            return card_for(s, seed, rng.choice(pool[:12]))  # Jellyseerr ranks them; stay near the top
    raise Unavailable("no new recommendations right now")


async def request(client: httpx.AsyncClient, s: Settings, media: str, tmdb: int) -> dict[str, Any]:
    """File a Jellyseerr request (all seasons for a series). Jellyseerr passes it to Radarr or Sonarr."""
    body: dict[str, Any] = {"mediaType": media, "mediaId": tmdb}
    if media == "tv":
        body["seasons"] = "all"
    r = await client.post(f"{s.jellyseerr_url}/api/v1/request", json=body, headers=_headers(s), timeout=20)
    if r.status_code == 409:
        return {"status": "already requested"}
    r.raise_for_status()
    j = r.json()
    _cache._data.clear()  # it's requested now: don't offer it again
    return {"status": "requested", "id": j.get("id")}
