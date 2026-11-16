"""Where you are, where you've been, where you're going.

Feeds the Wikipedia curator: home, places in your recent photos, and
upcoming trips from the household travel service.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("liessure.context")


@dataclass
class Place:
    label: str                     # shown to the model, e.g. "near your photos from 2026-10-02 (Kyoto)"
    coords: tuple[float, float] | None = None
    query: str = ""                # text search when there are no coordinates


@dataclass
class Trip:
    name: str
    start: date | None = None
    end: date | None = None
    places: list[str] = field(default_factory=list)
    coords: list[tuple[float, float]] = field(default_factory=list)


def _date(v: Any) -> date | None:
    if not isinstance(v, str) or len(v) < 10:
        return None
    try:
        return datetime.fromisoformat(v[:10]).date()
    except ValueError:
        return None


def _coords(d: dict) -> tuple[float, float] | None:
    for la, lo in (("lat", "lon"), ("lat", "lng"), ("latitude", "longitude")):
        if isinstance(d.get(la), (int, float)) and isinstance(d.get(lo), (int, float)):
            return float(d[la]), float(d[lo])
    return None


PLACE_KEYS = ("destination", "destinations", "places", "locations", "cities", "city", "region", "country", "stops")


def parse_trips(body: Any) -> list[Trip]:
    """Best effort over the travel API's trip list: names, dates, places, coordinates."""
    rows = body.get("trips") if isinstance(body, dict) else body
    trips = []
    for t in rows or []:
        if not isinstance(t, dict):
            continue
        name = t.get("name") or t.get("title") or t.get("slug") or ""
        start = next((_date(v) for k, v in t.items() if "start" in k and _date(v)), None)
        end = next((_date(v) for k, v in t.items() if "end" in k and _date(v)), None)
        places: list[str] = []
        coords: list[tuple[float, float]] = []
        for key in PLACE_KEYS:
            v = t.get(key)
            for item in v if isinstance(v, list) else [v]:
                if isinstance(item, str) and item.strip():
                    places.append(item.strip())
                elif isinstance(item, dict):
                    label = item.get("name") or item.get("title") or item.get("city")
                    if isinstance(label, str):
                        places.append(label)
                    c = _coords(item)
                    if c:
                        coords.append(c)
        if name:
            trips.append(Trip(name=str(name), start=start, end=end, places=places[:4], coords=coords[:4]))
    return trips


async def upcoming_trips(client: httpx.AsyncClient, s: Settings, today: date | None = None) -> list[Trip]:
    if not (s.travel.url and s.travel_token):
        return []
    today = today or date.today()
    try:
        r = await client.get(f"{s.travel.url}/api/trips", headers={"Authorization": f"Bearer {s.travel_token}"})
        r.raise_for_status()
        trips = parse_trips(r.json())
    except (httpx.HTTPError, ValueError) as e:
        log.warning("travel: %s", e)
        return []
    soon = [t for t in trips if (t.end or t.start or today) >= today]
    soon.sort(key=lambda t: t.start or date.max)
    return soon[:3]


async def recent_photo_places(client: httpx.AsyncClient, s: Settings, days: int = 60, limit: int = 4) -> list[Place]:
    """Distinct places (to ~10 km) in photos from the last `days` days."""
    if not (s.immich.url and s.immich_api_key):
        return []
    since = (datetime.now() - timedelta(days=days)).isoformat()
    try:
        r = await client.post(
            f"{s.immich.url}/api/search/metadata",
            json={"takenAfter": since, "withExif": True, "size": 250, "type": "IMAGE"},
            headers={"x-api-key": s.immich_api_key},
        )
        r.raise_for_status()
        items = ((r.json().get("assets") or {}).get("items")) or []
    except (httpx.HTTPError, ValueError, AttributeError) as e:
        log.warning("immich recent places: %s", e)
        return []
    seen: dict[tuple[float, float], Place] = {}
    for a in items:
        exif = a.get("exifInfo") or {}
        c = _coords({"lat": exif.get("latitude"), "lon": exif.get("longitude")})
        if not c:
            continue
        key = (round(c[0], 1), round(c[1], 1))
        if key in seen:
            continue
        when = (a.get("localDateTime") or a.get("fileCreatedAt") or "")[:10]
        where = exif.get("city") or exif.get("state") or exif.get("country") or ""
        seen[key] = Place(f"near your photos from {when}" + (f" ({where})" if where else ""), c)
    if s.home_coords:
        # Everyday photos at home aren't news; the home geosearch already covers them.
        hl, ho = s.home_coords
        seen = {k: p for k, p in seen.items() if abs(k[0] - hl) > 0.15 or abs(k[1] - ho) > 0.2}
    return list(seen.values())[:limit]


async def wikipedia_places(client: httpx.AsyncClient, s: Settings) -> list[Place]:
    places: list[Place] = []
    if s.home_coords:
        places.append(Place(f"near home ({s.home_name})", s.home_coords))
    places += await recent_photo_places(client, s)
    for t in await upcoming_trips(client, s):
        when = f" starting {t.start.isoformat()}" if t.start else ""
        label = f"upcoming trip: {t.name}{when}"
        for c in t.coords[:2]:
            places.append(Place(label, c))
        for p in t.places[:2] or [t.name]:
            places.append(Place(label, query=p))
    return places
