"""Card sources. Each one returns a single random Card or raises Unavailable.

A source talks to its service with the server's own credentials, so the
browser and the Android app never hold a Jellyfin token or Immich key.
Images from household services are routed through /img (see main.py) for
the same reason.
"""

from __future__ import annotations

import base64
import random
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlencode

import httpx

from .config import Settings


STORE: Any = None  # app.store.Store, set by main at startup


class Unavailable(Exception):
    """The source is not configured, or had nothing to give this time."""


@dataclass
class Card:
    kind: str
    label: str
    title: str
    url: str
    subtitle: str = ""
    blurb: str = ""
    image: str = ""
    # Extra buttons. {"label": ..., "url": ...} or {"label": ..., "app": "<android package>"}
    actions: list[dict[str, str]] = field(default_factory=list)
    # Something to consume right in the feed: {"type": "audio"|"video", "src": ...}
    embed: dict[str, str] = field(default_factory=dict)
    # Stable id within its kind, reported back by /api/event when you open or play it.
    ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def proxied(src: str) -> str:
    return "/img?" + urlencode({"src": src}) if src else ""


def _clip(text: str | None, n: int = 320) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


class _TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self._data: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any:
        hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    def put(self, key: Any, value: Any) -> None:
        self._data[key] = (time.monotonic(), value)


# --------------------------------------------------------------------- auth


def auth_headers_for(settings: Settings, url: str) -> dict[str, str] | None:
    """Headers to attach when fetching `url`, or None if the host isn't ours.

    Also the allowlist for the /img proxy: only these bases are fetchable.
    """
    if settings.jellyfin.url and url.startswith(settings.jellyfin.url + "/"):
        return _jellyfin_headers(settings)
    if settings.immich.url and url.startswith(settings.immich.url + "/"):
        return {"x-api-key": settings.immich_api_key}
    if settings.romm.url and url.startswith(settings.romm.url + "/"):
        return _romm_headers(settings)
    if settings.invidious.url and url.startswith(settings.invidious.url + "/"):
        return {}
    return None


def _jellyfin_headers(s: Settings) -> dict[str, str]:
    return {
        "Authorization": (
            'MediaBrowser Client="Liessure", Device="server", DeviceId="liessure", '
            f'Version="0.1", Token="{s.jellyfin_token}"'
        )
    }


def _romm_headers(s: Settings) -> dict[str, str]:
    if s.romm_token:
        return {"Authorization": f"Bearer {s.romm_token}"}
    if s.romm_username:
        raw = f"{s.romm_username}:{s.romm_password}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    return {}


# ---------------------------------------------------------------- wikipedia


async def _wiki_random(client: httpx.AsyncClient, lang: str) -> dict[str, Any]:
    r = await client.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/random/summary")
    r.raise_for_status()
    return r.json()


def _wiki_card(data: dict[str, Any], kind: str, label: str) -> Card:
    urls = data.get("content_urls") or {}
    page = (urls.get("mobile") or urls.get("desktop") or {}).get("page")
    if not page:
        raise Unavailable("wikipedia summary had no page url")
    return Card(
        kind=kind,
        label=label,
        title=data.get("title") or "Wikipedia",
        subtitle=data.get("description") or "",
        blurb=_clip(data.get("extract"), 1500),
        image=((data.get("originalimage") or data.get("thumbnail") or {}).get("source") or ""),
        url=page,
    )


async def wikipedia(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    # Random articles are often stubs. Draw a few and keep the meatiest.
    best: dict[str, Any] | None = None
    for _ in range(3):
        data = await _wiki_random(client, s.wikipedia_lang)
        if data.get("type") == "disambiguation":
            continue
        if best is None or len(data.get("extract") or "") > len(best.get("extract") or ""):
            best = data
        if len(best.get("extract") or "") > 300 and best.get("thumbnail"):
            break
    if best is None:
        raise Unavailable("only disambiguation pages")
    return _wiki_card(best, "wikipedia", "Wikipedia")


# ----------------------------------------------------------------- jellyfin


def _jellyfin_link(s: Settings, item: dict[str, Any]) -> str:
    q = f"id={item['Id']}"
    if item.get("ServerId"):
        q += f"&serverId={item['ServerId']}"
    return f"{s.jellyfin.link_base}/web/#/details?{q}"


async def _jellyfin_random(
    client: httpx.AsyncClient, s: Settings, item_type: str, unplayed: bool
) -> dict[str, Any]:
    if not (s.jellyfin.url and s.jellyfin_token):
        raise Unavailable("jellyfin not configured")
    params = {
        "IncludeItemTypes": item_type,
        "Recursive": "true",
        "SortBy": "Random",
        "Limit": "1",
        "Fields": "Overview,Genres,ProductionYear,RemoteTrailers",
        "EnableImageTypes": "Primary",
    }
    base = f"{s.jellyfin.url}/Items"
    if s.jellyfin_user_id:
        params["userId"] = s.jellyfin_user_id
        if unplayed:
            params["IsPlayed"] = "false"
    headers = _jellyfin_headers(s)
    r = await client.get(base, params=params, headers=headers)
    r.raise_for_status()
    items = r.json().get("Items") or []
    if not items and unplayed and "IsPlayed" in params:
        params.pop("IsPlayed")
        r = await client.get(base, params=params, headers=headers)
        r.raise_for_status()
        items = r.json().get("Items") or []
    if not items:
        raise Unavailable(f"no jellyfin {item_type}")
    return items[0]


def _jellyfin_image(s: Settings, item_id: str) -> str:
    return proxied(f"{s.jellyfin.url}/Items/{item_id}/Images/Primary?maxWidth=720&quality=85")


async def _jellyfin_video(
    client: httpx.AsyncClient, s: Settings, item_type: str, kind: str, label: str
) -> Card:
    item = await _jellyfin_random(client, s, item_type, unplayed=True)
    bits = [str(item["ProductionYear"])] if item.get("ProductionYear") else []
    bits += (item.get("Genres") or [])[:3]
    return Card(
        kind=kind,
        label=label,
        title=item.get("Name") or label,
        subtitle=" · ".join(bits),
        blurb=_clip(item.get("Overview")),
        image=_jellyfin_image(s, item["Id"]) if (item.get("ImageTags") or {}).get("Primary") else "",
        url=_jellyfin_link(s, item),
        embed=_trailer_embed(s, item),
    )


def _youtube_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else ""


def _trailer_embed(s: Settings, item: dict[str, Any]) -> dict[str, str]:
    if not s.invidious.link_base:
        return {}
    for t in item.get("RemoteTrailers") or []:
        vid = _youtube_id(t.get("Url", ""))
        if vid:
            return {"type": "video", "label": "Watch the trailer", "src": f"{s.invidious.link_base}/embed/{vid}?autoplay=1"}
    return {}


async def movie(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    return await _jellyfin_video(client, s, "Movie", "movie", "Movie on Jellyfin")


async def show(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    return await _jellyfin_video(client, s, "Series", "show", "Series on Jellyfin")


async def song(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    item = await _jellyfin_random(client, s, "Audio", unplayed=False)
    artist = item.get("AlbumArtist") or ", ".join(item.get("Artists") or [])
    sub = " · ".join(x for x in (artist, item.get("Album")) if x)
    if (item.get("ImageTags") or {}).get("Primary"):
        image = _jellyfin_image(s, item["Id"])
    elif item.get("AlbumId"):
        image = _jellyfin_image(s, item["AlbumId"])
    else:
        image = ""
    actions = []
    if s.music_app_package:
        actions.append({"label": f"Open {s.music_app_name}", "app": s.music_app_package})
    if item.get("AlbumId"):
        actions.append(
            {"label": "Album", "url": _jellyfin_link(s, {"Id": item["AlbumId"], "ServerId": item.get("ServerId")})}
        )
    return Card(
        kind="song",
        label="Song",
        title=item.get("Name") or "Song",
        subtitle=sub,
        image=image,
        url=_jellyfin_link(s, item),
        actions=actions,
        embed={"type": "audio", "label": "Play here", "src": f"/stream/audio/{item['Id']}"},
    )


# ------------------------------------------------------------------- immich


def _immich_card(s: Settings, asset: dict[str, Any], subtitle: str) -> Card:
    exif = asset.get("exifInfo") or {}
    place = ", ".join(x for x in (exif.get("city"), exif.get("state"), exif.get("country")) if x)
    when = (asset.get("localDateTime") or asset.get("fileCreatedAt") or "")[:10]
    parts = [p for p in (subtitle, when, place) if p]
    return Card(
        kind="photo",
        label="Photo in Immich",
        title=place or when or "A photo",
        subtitle=" · ".join(parts[1:] if not subtitle else parts),
        blurb=_clip(exif.get("description")),
        image=proxied(f"{s.immich.url}/api/assets/{asset['id']}/thumbnail?size=preview"),
        url=f"{s.immich.link_base}/photos/{asset['id']}",
    )


async def _immich_memory(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    r = await client.get(
        f"{s.immich.url}/api/memories",
        params={"for": date.today().isoformat()},
        headers={"x-api-key": s.immich_api_key},
    )
    r.raise_for_status()
    memories = [m for m in r.json() if m.get("assets")]
    if not memories:
        raise Unavailable("no memories today")
    mem = rng.choice(memories)
    asset = rng.choice(mem["assets"])
    year = (mem.get("data") or {}).get("year")
    label = "On this day"
    if year:
        ago = date.today().year - int(year)
        label = f"On this day, {ago} year{'s' if ago != 1 else ''} ago"
    card = _immich_card(s, asset, label)
    card.label = "Memory in Immich"
    return card


async def photo(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    if not (s.immich.url and s.immich_api_key):
        raise Unavailable("immich not configured")
    if rng.random() < 0.35:
        try:
            return await _immich_memory(client, s, rng)
        except (Unavailable, httpx.HTTPError, KeyError, ValueError):
            pass
    r = await client.post(
        f"{s.immich.url}/api/search/random",
        json={"size": 1, "type": "IMAGE", "withExif": True},
        headers={"x-api-key": s.immich_api_key},
    )
    r.raise_for_status()
    assets = r.json()
    if not assets:
        raise Unavailable("immich returned nothing")
    return _immich_card(s, assets[0], "")


# --------------------------------------------------------------------- romm

_romm_total = _TTLCache(600)


def _romm_page(body: Any) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(body, list):
        return body, len(body)
    return body.get("items") or [], body.get("total")


async def game(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    if not (s.romm.url and (s.romm_token or s.romm_username)):
        raise Unavailable("romm not configured")
    headers = _romm_headers(s)
    url = f"{s.romm.url}/api/roms"
    total = _romm_total.get("total")
    if total is None:
        r = await client.get(url, params={"limit": 1, "offset": 0}, headers=headers)
        r.raise_for_status()
        items, total = _romm_page(r.json())
        if total is None:
            total = len(items)
        _romm_total.put("total", total)
    if not total:
        raise Unavailable("romm library is empty")
    r = await client.get(url, params={"limit": 1, "offset": rng.randrange(total)}, headers=headers)
    r.raise_for_status()
    items, _ = _romm_page(r.json())
    if not items:
        raise Unavailable("romm page was empty")
    rom = items[0]

    cover = rom.get("path_cover_large") or rom.get("path_cover_l") or rom.get("path_cover_small") or ""
    if cover:
        cover = cover.lstrip("/")
        if not cover.startswith("assets/"):
            cover = "assets/romm/resources/" + cover
        image = proxied(f"{s.romm.url}/{cover}")
    else:
        image = rom.get("url_cover") or ""

    platform = rom.get("platform_display_name") or rom.get("platform_name") or rom.get("platform_slug") or ""
    link = f"{s.romm.link_base}/rom/{rom['id']}"
    return Card(
        kind="game",
        label="Game on RomM",
        title=rom.get("name") or rom.get("fs_name_no_ext") or "A game",
        subtitle=platform,
        blurb=_clip(rom.get("summary")),
        image=image,
        url=link,
        actions=[{"label": "Play in browser", "url": f"{link}/ejs"}],
    )


# ---------------------------------------------------------------- invidious

_inv_cache = _TTLCache(3600)


async def _invidious_search(client: httpx.AsyncClient, s: Settings, query: str, page: int) -> list[dict[str, Any]]:
    key = (query, page)
    hit = _inv_cache.get(key)
    if hit is not None:
        return hit
    r = await client.get(
        f"{s.invidious.url}/api/v1/search",
        params={"q": query, "type": "video", "page": page},
    )
    r.raise_for_status()
    # Skip Shorts and clips: the point is to trade a scroll for something worth watching.
    videos = [
        v for v in r.json()
        if v.get("type") == "video" and v.get("videoId") and (v.get("lengthSeconds") or 0) >= 180
        and not v.get("liveNow")
    ]
    _inv_cache.put(key, videos)
    return videos


def _duration(sec: int) -> str:
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _video_card(s: Settings, v: dict[str, Any], kind: str, label: str) -> Card:
    vid = v["videoId"]
    bits = [v.get("author") or ""]
    if v.get("lengthSeconds"):
        bits.append(_duration(v["lengthSeconds"]))
    return Card(
        kind=kind,
        label=label,
        title=v.get("title") or "A video",
        subtitle=" · ".join(b for b in bits if b),
        blurb=_clip(v.get("description"), 240),
        image=proxied(f"{s.invidious.url}/vi/{quote(vid)}/mqdefault.jpg"),
        url=f"{s.invidious.link_base}/watch?v={quote(vid)}",
        embed={"type": "video", "label": "Play here", "src": f"{s.invidious.link_base}/embed/{quote(vid)}?autoplay=1"},
        ref=vid,
    )


async def _random_video(client: httpx.AsyncClient, s: Settings, rng: random.Random, queries: list[str]) -> dict[str, Any]:
    if not s.invidious.url:
        raise Unavailable("invidious not configured")
    if not queries:
        raise Unavailable("no video topics")
    videos = await _invidious_search(client, s, rng.choice(queries), rng.randint(1, 3))
    if not videos:
        raise Unavailable("no videos for topic")
    return rng.choice(videos)


async def video(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    # Mostly the curated pool; a share of raw search keeps some surprise in it.
    if STORE is not None and rng.random() < 0.8:
        pick = STORE.take("video", rng.random())
        if pick:
            v = {"videoId": pick["ref"], "title": pick["title"], "author": pick["author"],
                 "lengthSeconds": pick["seconds"]}
            card = _video_card(s, v, "video", "Picked for you")
            card.blurb = pick["reason"]
            return card
    v = await _random_video(client, s, rng, s.invidious_topics)
    return _video_card(s, v, "video", "Video")


# ----------------------------------------------------------------- language


def _resources(s: Settings) -> list[tuple[str, str, str]]:
    if s.language_name.lower() != "telugu":
        return []
    return [
        ("Andhra Bharati dictionaries", "Look up a word you half-remember.", "https://www.andhrabharati.com/dictionary/"),
        ("Telugu on Wiktionary", "Browse Telugu words with script, transliteration and usage.", "https://en.wiktionary.org/wiki/Category:Telugu_lemmas"),
        ("Telugu sentences on Tatoeba", "Read a few real sentences and their translations.", "https://tatoeba.org/en/sentences/show_all_in/tel/none"),
        ("Forvo: Telugu pronunciation", "Hear native speakers say everyday words.", "https://forvo.com/languages/te/"),
        ("Telugu script on Omniglot", "Refresh the vowels, consonants and vowel signs.", "https://www.omniglot.com/writing/telugu.htm"),
    ]


async def language(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    lang = s.language_name
    options: list[Callable[[], Awaitable[Card]]] = []

    if s.language_practice_url:
        async def drill() -> Card:
            return Card(
                kind="language",
                label=f"{lang} practice",
                title=f"Five minutes of {lang} drills",
                subtitle="Your spaced-repetition queue",
                blurb="Clear whatever is due. It's shorter than a front page.",
                url=s.language_practice_url,
            )
        options += [drill, drill]  # the drill is the best use of the minute; weight it

    if s.language_wikipedia:
        async def wiki() -> Card:
            data = await _wiki_random(client, s.language_wikipedia)
            card = _wiki_card(data, "language", f"{lang} Wikipedia")
            card.subtitle = card.subtitle or "Read the first sentence out loud"
            return card
        options.append(wiki)

    if s.invidious.url and s.language_video_queries:
        async def vid() -> Card:
            v = await _random_video(client, s, rng, s.language_video_queries)
            return _video_card(s, v, "language", f"{lang} video")
        options.append(vid)

    resources = _resources(s)
    if resources:
        async def resource() -> Card:
            title, blurb, url = rng.choice(resources)
            return Card(kind="language", label=f"{lang} resource", title=title, blurb=blurb, url=url)
        options.append(resource)

    if not options:
        raise Unavailable("no language sources")
    rng.shuffle(options)
    last: Exception | None = None
    for opt in options[:2]:
        try:
            return await opt()
        except (Unavailable, httpx.HTTPError, KeyError, ValueError) as e:
            last = e
    raise Unavailable(f"language sources failed: {last}")


# ----------------------------------------------------------------- registry

SOURCES: dict[str, Callable[[httpx.AsyncClient, Settings, random.Random], Awaitable[Card]]] = {
    "wikipedia": wikipedia,
    "game": game,
    "video": video,
    "movie": movie,
    "show": show,
    "song": song,
    "photo": photo,
    "language": language,
}


def configured(s: Settings) -> dict[str, bool]:
    return {
        "wikipedia": True,
        "game": bool(s.romm.url and (s.romm_token or s.romm_username)),
        "video": bool(s.invidious.url and s.invidious_topics),
        "movie": bool(s.jellyfin.url and s.jellyfin_token),
        "show": bool(s.jellyfin.url and s.jellyfin_token),
        "song": bool(s.jellyfin.url and s.jellyfin_token),
        "photo": bool(s.immich.url and s.immich_api_key),
        "language": bool(
            s.language_practice_url or s.language_wikipedia or _resources(s)
            or (s.invidious.url and s.language_video_queries)
        ),
    }
