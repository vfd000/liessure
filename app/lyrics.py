"""Song lyrics: Jellyfin's own (a .lrc beside the file, or embedded), else LRCLIB (lrclib.net, open, no key).

Lines come back as [{"t": seconds or None, "text": "..."}]; synced lyrics have a time on every line, so the
song card can follow along while the song plays in it.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from .config import Settings
from .sources import _jellyfin_headers

LRCLIB = "https://lrclib.net/api"
_LRC = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s?(.*)$")


def parse_lrc(text: str) -> list[dict[str, Any]]:
    """LRC ("[01:23.45] words") to timed lines; plain text to untimed ones."""
    lines = []
    for raw in (text or "").splitlines():
        m = _LRC.match(raw.strip())
        if m:
            lines.append({"t": round(int(m.group(1)) * 60 + float(m.group(2)), 2), "text": m.group(3).strip()})
        elif not re.match(r"^\[[a-z]+:.*\]$", raw.strip()):  # skip [ar:...] style tags
            lines.append({"t": None, "text": raw.strip()})
    while lines and not lines[0]["text"]:
        lines.pop(0)
    return lines


async def song_info(client: httpx.AsyncClient, s: Settings, item_id: str) -> dict[str, Any]:
    params = {"Ids": item_id, "Fields": "Path"}
    if s.jellyfin_user_id:
        params["UserId"] = s.jellyfin_user_id
    r = await client.get(f"{s.jellyfin.url}/Items", params=params, headers=_jellyfin_headers(s))
    r.raise_for_status()
    items = r.json().get("Items") or []
    if not items:
        raise ValueError("no such song")
    it = items[0]
    return {"title": it.get("Name") or "", "album": it.get("Album") or "",
            "artist": it.get("AlbumArtist") or ", ".join(it.get("Artists") or []),
            "seconds": round((it.get("RunTimeTicks") or 0) / 10_000_000)}


async def from_jellyfin(client: httpx.AsyncClient, s: Settings, item_id: str) -> list[dict[str, Any]]:
    """Jellyfin 10.9+: /Audio/{id}/Lyrics, Start in ticks (100 ns) when synced. [] when it has none."""
    r = await client.get(f"{s.jellyfin.url}/Audio/{item_id}/Lyrics", headers=_jellyfin_headers(s))
    if r.status_code == 404:
        return []
    r.raise_for_status()
    out = []
    for line in r.json().get("Lyrics") or []:
        start = line.get("Start")
        out.append({"t": round(start / 10_000_000, 2) if isinstance(start, (int, float)) else None,
                    "text": str(line.get("Text") or "").strip()})
    return out if any(x["text"] for x in out) else []


async def from_lrclib(client: httpx.AsyncClient, s: Settings, song: dict[str, Any]) -> list[dict[str, Any]]:
    """An exact match (artist, title, album, length within 2 s), else the best search hit."""
    headers = {"User-Agent": s.user_agent}
    params = {"artist_name": song["artist"], "track_name": song["title"]}
    hit = None
    if song.get("seconds"):
        r = await client.get(f"{LRCLIB}/get", params={**params, "album_name": song.get("album", ""),
                                                      "duration": song["seconds"]}, headers=headers, timeout=15)
        if r.status_code == 200:
            hit = r.json()
        elif r.status_code != 404:
            r.raise_for_status()
    if not hit:
        r = await client.get(f"{LRCLIB}/search", params=params, headers=headers, timeout=15)
        r.raise_for_status()
        found = [h for h in r.json() or [] if h.get("syncedLyrics") or h.get("plainLyrics")]
        if song.get("seconds"):
            found.sort(key=lambda h: abs((h.get("duration") or 0) - song["seconds"]))
        hit = found[0] if found else None
    if not hit or hit.get("instrumental"):
        return []
    return parse_lrc(hit.get("syncedLyrics") or hit.get("plainLyrics") or "")


async def lyrics(client: httpx.AsyncClient, s: Settings, item_id: str) -> dict[str, Any]:
    lines: list[dict[str, Any]] = []
    source = ""
    try:
        lines = await from_jellyfin(client, s, item_id)
        source = "Jellyfin" if lines else ""
    except httpx.HTTPError:
        pass
    song = await song_info(client, s, item_id)
    if not lines and song["title"] and song["artist"]:
        lines = await from_lrclib(client, s, song)
        source = "LRCLIB" if lines else ""
    return {"lines": lines, "synced": bool(lines) and all(x["t"] is not None for x in lines if x["text"]),
            "source": source, "title": song["title"], "artist": song["artist"]}
