from __future__ import annotations

import asyncio
import logging
import random
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import sources
from .config import KINDS, Settings

log = logging.getLogger("detour")
STATIC = Path(__file__).parent / "static"
PER_CARD_TIMEOUT = 8.0


def pick_kinds(weights: dict[str, int], available: dict[str, bool], n: int, rng: random.Random) -> list[str]:
    pool = [k for k in KINDS if available.get(k) and weights.get(k, 0) > 0]
    if not pool:
        return []
    picked: list[str] = []
    for _ in range(n):
        choices = [k for k in pool if not picked or k != picked[-1]] or pool
        picked.append(rng.choices(choices, weights=[weights[k] for k in choices])[0])
    return picked


def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
            transport=transport,
        )
        try:
            yield
        finally:
            await app.state.client.aclose()

    app = FastAPI(title="Detour", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings

    async def one(client: httpx.AsyncClient, kind: str, rng: random.Random) -> dict | None:
        try:
            card = await asyncio.wait_for(sources.SOURCES[kind](client, settings, rng), PER_CARD_TIMEOUT)
            return card.to_dict()
        except (sources.Unavailable, httpx.HTTPError, asyncio.TimeoutError, KeyError, ValueError, TypeError) as e:
            log.warning("source %s failed: %s: %s", kind, type(e).__name__, e)
            return None

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/api/sources")
    async def list_sources():
        avail = sources.configured(settings)
        return {
            "language": settings.language_name,
            "sources": {k: {"configured": avail[k], "weight": settings.weights.get(k, 0)} for k in KINDS},
        }

    @app.get("/api/feed")
    async def feed(
        request: Request,
        n: int = Query(6, ge=1, le=20),
        kinds: str = Query("", description="comma-separated subset of kinds"),
    ):
        rng = random.Random()
        avail = sources.configured(settings)
        if kinds:
            wanted = {k.strip() for k in kinds.split(",")}
            avail = {k: v and k in wanted for k, v in avail.items()}
        plan = pick_kinds(settings.weights, avail, n, rng)
        client = request.app.state.client
        cards = await asyncio.gather(*(one(client, k, rng) for k in plan))
        out = [c for c in cards if c]
        # Something always beats an empty screen.
        if not out and plan:
            fallback = await one(client, "wikipedia", rng)
            if fallback:
                out.append(fallback)
        return {"cards": out}

    @app.get("/api/card/{kind}")
    async def card(request: Request, kind: str):
        if kind not in sources.SOURCES:
            raise HTTPException(404, "unknown kind")
        c = await one(request.app.state.client, kind, random.Random())
        if not c:
            raise HTTPException(503, f"{kind} has nothing right now")
        return c

    @app.get("/img")
    async def img(request: Request, src: str):
        headers = sources.auth_headers_for(settings, src)
        if headers is None or ".." in src:
            raise HTTPException(400, "not an allowed image source")
        try:
            r = await request.app.state.client.get(src, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(502, "image fetch failed")
        ctype = r.headers.get("content-type", "")
        if r.status_code != 200 or not ctype.startswith("image/"):
            raise HTTPException(502, "image fetch failed")
        return Response(r.content, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
