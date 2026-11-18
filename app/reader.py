"""Phone-friendly reading for Phrack articles.

Phrack publishes each article as plain text hard-wrapped at ~75 columns,
which a phone wraps again into a ragged mess. This fetches the text version,
joins prose back into paragraphs, turns "--[ 1 - Intro" lines into headings,
and keeps code, tables and diagrams as scrollable monospace blocks.
"""

from __future__ import annotations

import html
import re
from typing import Any

import httpx

PHRACK = "https://phrack.org"
SLUG = re.compile(r"^[a-z0-9_-]{1,160}$")
_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S)
_TAG = re.compile(r"<[^>]+>")
_HEADING = re.compile(r"^\s*-{1,4}\[\s*(.+?)\s*\]?\s*-*\s*$")
_BANNER = re.compile(r"^\|=[-=\[\] ]*(.*?)[-=\] ]*=\|$")
_CODEISH = set("{}();=<>\\/|*#$&[]")


def text_url(issue: int, slug: str) -> str:
    """The plain-text page: the table of contents links the "_md" render, the text is without it."""
    return f"{PHRACK}/issues/{issue}/{slug.removesuffix('_md')}.html"


async def fetch_text(client: httpx.AsyncClient, issue: int, slug: str) -> str:
    r = await client.get(text_url(issue, slug), headers={"User-Agent": "Mozilla/5.0 (Liessure)"})
    r.raise_for_status()
    m = _PRE.search(r.text)
    if not m:
        raise ValueError("no article text on that page")
    return html.unescape(_TAG.sub("", m.group(1)))


def _is_code(lines: list[str]) -> bool:
    if any(l.startswith(("  ", "\t")) for l in lines):
        return True
    text = "".join(lines)
    return bool(text) and sum(ch in _CODEISH for ch in text) / len(text) > 0.06


def blocks(text: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """({"title", "author"}, [(type, text)]) with type heading | p | pre."""
    meta: dict[str, str] = {}
    out: list[tuple[str, str]] = []
    for para in re.split(r"\n\s*\n", text.replace("\r", "")):
        lines = [l.rstrip() for l in para.split("\n") if l.strip()]
        if not lines:
            continue
        banner = [_BANNER.match(l.strip()) for l in lines]
        if all(banner):
            names = [m.group(1).strip(" []=-") for m in banner if m.group(1).strip(" []=-")]
            if names and "title" not in meta:
                meta["title"] = names[0]
                if len(names) > 1:
                    meta["author"] = names[1]
            continue
        h = _HEADING.match(lines[0])
        if h and len(lines[0]) < 90:
            out.append(("heading", h.group(1).strip(" -[]")))
            lines = lines[1:]
            if not lines:
                continue
        if _is_code(lines):
            out.append(("pre", "\n".join(lines)))
        else:
            out.append(("p", " ".join(l.strip() for l in lines)))
    return meta, out


def page(issue: int, slug: str, text: str) -> str:
    meta, parts = blocks(text)
    title = meta.get("title") or slug.replace("-", " ").removesuffix("_md")
    body = []
    for kind, t in parts:
        e = html.escape(t)
        if kind == "heading":
            body.append(f"<h2>{e}</h2>")
        elif kind == "pre":
            body.append(f"<pre>{e}</pre>")
        else:
            body.append(f"<p>{e}</p>")
    original = html.escape(f"{PHRACK}/issues/{issue}/{slug}.html")
    by = f"<div class=\"subtitle\">{html.escape(meta['author'])} · Phrack #{issue}</div>" if meta.get("author") \
        else f"<div class=\"subtitle\">Phrack #{issue}</div>"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{html.escape(title)}</title><link rel="stylesheet" href="/style.css">
<style>
.reader {{ max-width: 44rem; margin: 0 auto; padding: 16px 16px 64px; line-height: 1.6; font-size: 17px; }}
.reader h1 {{ font-size: 24px; line-height: 1.25; margin: 8px 0 4px; }}
.reader h2 {{ font-size: 19px; margin: 28px 0 8px; }}
.reader pre {{ overflow-x: auto; font-size: 13px; line-height: 1.4; padding: 10px; border-radius: 8px;
  background: rgba(127, 127, 127, 0.12); }}
.reader .top {{ display: flex; gap: 12px; margin-bottom: 12px; font-size: 14px; }}
</style></head><body><main class="reader">
<div class="top"><a href="/">← Liessure</a><a href="{original}">Original</a></div>
<h1>{html.escape(title)}</h1>{by}
{"".join(body)}
<p><a href="{original}">Read it on phrack.org</a></p>
</main></body></html>"""
