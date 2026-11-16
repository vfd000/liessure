"""Card sources. Each one returns a single random Card or raises Unavailable.

A source talks to its service with the server's own credentials, so the
browser and the Android app never hold a Jellyfin token or Immich key.
Images from household services are routed through /img (see main.py) for
the same reason.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import logging
import random
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlencode

import httpx

from .config import Settings, language_queries


log = logging.getLogger("liessure.sources")

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
    # Set when the card came from the LLM-curated pool: why it was picked for you.
    why: str = ""

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
        ref=(data.get("titles") or {}).get("canonical") or data.get("title") or "",
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
        ref=item["Id"],
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
    return song_card(s, await _jellyfin_random(client, s, "Audio", unplayed=False))


def song_artist(item: dict[str, Any]) -> str:
    return item.get("AlbumArtist") or ", ".join(item.get("Artists") or [])


def song_card(s: Settings, item: dict[str, Any]) -> Card:
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
        ref=item["Id"],
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
        ref=asset["id"],
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
    return game_card(s, items[0])


async def romm_page(client: httpx.AsyncClient, s: Settings, limit: int, offset: int) -> tuple[list[dict[str, Any]], int | None]:
    r = await client.get(f"{s.romm.url}/api/roms", params={"limit": limit, "offset": offset}, headers=_romm_headers(s))
    r.raise_for_status()
    return _romm_page(r.json())


def rom_platform(rom: dict[str, Any]) -> str:
    return rom.get("platform_display_name") or rom.get("platform_name") or rom.get("platform_slug") or ""


def game_card(s: Settings, rom: dict[str, Any]) -> Card:
    cover = rom.get("path_cover_large") or rom.get("path_cover_l") or rom.get("path_cover_small") or ""
    if cover:
        cover = cover.lstrip("/")
        if not cover.startswith("assets/"):
            cover = "assets/romm/resources/" + cover
        image = proxied(f"{s.romm.url}/{cover}")
    else:
        image = rom.get("url_cover") or ""

    link = f"{s.romm.link_base}/rom/{rom['id']}"
    actions = []
    if s.argosy:
        # Android only: Argosy (RomM's launcher) downloads if needed and starts the game.
        actions.append({"label": "Play in Argosy", "url": f"argosy://launch?romm_id={rom['id']}", "android": "1"})
    actions.append({"label": "Play in browser", "url": f"{link}/ejs"})
    return Card(
        kind="game",
        label="Game on RomM",
        title=rom.get("name") or rom.get("fs_name_no_ext") or "A game",
        subtitle=rom_platform(rom),
        blurb=_clip(rom.get("summary")),
        image=image,
        url=link,
        actions=actions,
        ref=str(rom["id"]),
    )


# ---------------------------------------------------------------- invidious

_inv_cache = _TTLCache(3600)
_nebula_cache = _TTLCache(3600)


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


def video_urls(s: Settings, vid: str) -> tuple[str, dict[str, str]]:
    """(primary url, secondary action) for a YouTube id, per VIDEO_LINK."""
    yt = f"https://www.youtube.com/watch?v={quote(vid)}"
    inv = f"{s.invidious.link_base}/watch?v={quote(vid)}"
    if s.video_link == "invidious" and s.invidious.link_base:
        return inv, {"label": "YouTube", "url": yt}
    return yt, {"label": "Invidious", "url": inv}


def nebula_card(item: dict[str, Any]) -> Card:
    return Card(
        kind="video",
        label="Nebula",
        title=item["title"],
        subtitle=item.get("author") or item.get("source") or "",
        blurb=_clip(item.get("summary"), 240),
        image=item.get("image") or "",
        url=item["url"],
        ref="nebula:" + hashlib.sha1(item["url"].encode()).hexdigest()[:16],
    )


async def nebula_items(client: httpx.AsyncClient, s: Settings) -> list[dict[str, Any]]:
    hit = _nebula_cache.get("all")
    if hit is not None:
        return hit
    items: list[dict[str, Any]] = []
    for url in s.nebula_feeds:
        try:
            r = await client.get(url)
            r.raise_for_status()
            items += parse_feed(r.content)
        except (httpx.HTTPError, ElementTree.ParseError) as e:
            log.warning("nebula feed %s failed: %s", url, e)
    _nebula_cache.put("all", items)
    return items


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
        url=video_urls(s, vid)[0],
        actions=[video_urls(s, vid)[1]],
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
    if s.nebula_feeds and (not s.invidious.url or rng.random() < 0.25):
        items = await nebula_items(client, s)
        if items:
            return nebula_card(rng.choice(items))
    v = await _random_video(client, s, rng, s.invidious_topics)
    return _video_card(s, v, "video", "Video")


# --------------------------------------------------------------------- news

_news_cache = _TTLCache(1800)
_TAG = re.compile(r"<[^>]+>")
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _text(el: Any) -> str:
    return html.unescape(_TAG.sub(" ", (el.text or "") if el is not None else "")).strip()


def _when(raw: str) -> float | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_feed(body: bytes) -> list[dict[str, Any]]:
    """RSS 2.0 or Atom -> [{title, url, summary, source, image, ts}]."""
    root = ElementTree.fromstring(body)
    out: list[dict[str, Any]] = []
    if root.tag == f"{{{NS['atom']}}}feed":
        source = _text(root.find("atom:title", NS))
        for e in root.findall("atom:entry", NS):
            link = next((l.get("href") for l in e.findall("atom:link", NS) if l.get("rel", "alternate") == "alternate"), "")
            out.append({
                "title": _text(e.find("atom:title", NS)), "url": link or "",
                "summary": _text(e.find("atom:summary", NS)) or _text(e.find("atom:content", NS)),
                "source": source, "image": "",
                "ts": _when(_text(e.find("atom:published", NS)) or _text(e.find("atom:updated", NS))),
            })
        return [i for i in out if i["title"] and i["url"].startswith("http")]
    channel = root.find("channel")
    if channel is None:
        return []
    source = _text(channel.find("title"))
    for it in channel.findall("item"):
        image = ""
        for path in ("media:content", "media:thumbnail"):
            m = it.find(path, NS)
            if m is not None and m.get("url"):
                image = m.get("url")
                break
        enc = it.find("enclosure")
        if not image and enc is not None and (enc.get("type") or "").startswith("image/"):
            image = enc.get("url") or ""
        author = _text(it.find("dc:creator", NS)) or _text(it.find("author"))
        out.append({
            "title": _text(it.find("title")), "url": _text(it.find("link")), "author": author,
            "summary": _text(it.find("description")), "source": source, "image": image,
            "ts": _when(_text(it.find("pubDate"))),
        })
    return [i for i in out if i["title"] and i["url"].startswith("http")]


async def news_items(client: httpx.AsyncClient, s: Settings) -> list[dict[str, Any]]:
    """Recent items across all feeds, cached for half an hour."""
    hit = _news_cache.get("all")
    if hit is not None:
        return hit
    results = await asyncio.gather(*(client.get(u) for u in s.news_feeds), return_exceptions=True)
    cutoff = time.time() - s.news_max_age_hours * 3600
    items: list[dict[str, Any]] = []
    for url, r in zip(s.news_feeds, results):
        try:
            if isinstance(r, Exception):
                raise r
            r.raise_for_status()
            items += [i for i in parse_feed(r.content) if i["ts"] is None or i["ts"] >= cutoff]
        except (httpx.HTTPError, ElementTree.ParseError) as e:
            log.warning("news feed %s failed: %s", url, e)
    _news_cache.put("all", items)
    return items


def news_card(item: dict[str, Any]) -> Card:
    return Card(
        kind="news",
        label=f"News · {item['source']}" if item.get("source") else "News",
        title=item["title"],
        blurb=_clip(item.get("summary"), 400),
        image=item.get("image") or "",
        url=item["url"],
        ref=hashlib.sha1(item["url"].encode()).hexdigest()[:16],
    )


async def news(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    if not s.news_feeds:
        raise Unavailable("no news feeds")
    items = await news_items(client, s)
    if not items:
        raise Unavailable("feeds had nothing recent")
    return news_card(rng.choice(items))


# ----------------------------------------------------------------- language


RESOURCES: dict[str, list[tuple[str, str, str]]] = {
    "te": [
        ("Andhra Bharati dictionaries", "Look up a word you half-remember.", "https://www.andhrabharati.com/dictionary/"),
        ("Telugu on Wiktionary", "Browse Telugu words with script, transliteration and usage.", "https://en.wiktionary.org/wiki/Category:Telugu_lemmas"),
        ("Telugu sentences on Tatoeba", "Read a few real sentences and their translations.", "https://tatoeba.org/en/sentences/show_all_in/tel/none"),
        ("Forvo: Telugu pronunciation", "Hear native speakers say everyday words.", "https://forvo.com/languages/te/"),
        ("Telugu script on Omniglot", "Refresh the vowels, consonants and vowel signs.", "https://www.omniglot.com/writing/telugu.htm"),
    ],
    "zh": [
        ("MDBG Chinese dictionary", "Look up a character: pinyin, tones, stroke order.", "https://www.mdbg.net/chinese/dictionary"),
        ("Mandarin on Wiktionary", "Browse Mandarin words with pinyin and usage.", "https://en.wiktionary.org/wiki/Category:Mandarin_lemmas"),
        ("Mandarin sentences on Tatoeba", "Read a few real sentences and their translations.", "https://tatoeba.org/en/sentences/show_all_in/cmn/none"),
        ("Forvo: Mandarin pronunciation", "Hear native speakers, tones and all.", "https://forvo.com/languages/zh/"),
        ("Chinese script on Omniglot", "How the writing system works, in one page.", "https://www.omniglot.com/chinese/"),
    ],
}


def learning_language(s: Settings, rng: random.Random) -> tuple[str, str]:
    """The current language (first) twice as often as each later one."""
    langs = s.languages_learning
    weights = [2] + [1] * (len(langs) - 1)
    return rng.choices(langs, weights=weights)[0]


async def language(client: httpx.AsyncClient, s: Settings, rng: random.Random) -> Card:
    if not s.languages_learning:
        raise Unavailable("no languages configured")
    lang, code = learning_language(s, rng)
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

    if code:
        async def wiki() -> Card:
            card = _wiki_card(await _wiki_random(client, code), "language", f"{lang} Wikipedia")
            card.subtitle = card.subtitle or "Read the first sentence out loud"
            return card
        options.append(wiki)

    if s.invidious.url:
        async def vid() -> Card:
            v = await _random_video(client, s, rng, language_queries(lang))
            return _video_card(s, v, "language", f"{lang} video")
        options.append(vid)

    if s.lingopie_url:
        async def lingopie() -> Card:
            return Card(
                kind="language", label="Lingopie", title="Watch an episode on Lingopie",
                blurb="One episode with dual subtitles. Tap the words you don't know.",
                url=s.lingopie_url,
            )
        options.append(lingopie)

    if RESOURCES.get(code):
        async def resource() -> Card:
            title, blurb, url = rng.choice(RESOURCES[code])
            return Card(kind="language", label=f"{lang} resource", title=title, blurb=blurb, url=url)
        options.append(resource)

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
    "news": news,
}


def configured(s: Settings) -> dict[str, bool]:
    return {
        "wikipedia": True,
        "game": bool(s.romm.url and (s.romm_token or s.romm_username)),
        "video": bool((s.invidious.url and s.invidious_topics) or s.nebula_feeds),
        "movie": bool(s.jellyfin.url and s.jellyfin_token),
        "show": bool(s.jellyfin.url and s.jellyfin_token),
        "song": bool(s.jellyfin.url and s.jellyfin_token),
        "photo": bool(s.immich.url and s.immich_api_key),
        "news": bool(s.news_feeds),
        "language": bool(s.languages_learning),
    }
