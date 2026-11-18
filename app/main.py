from __future__ import annotations

import asyncio
import logging
import random
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

from . import curator, profile, sources
from .config import KINDS, MAX_PER_LOAD, RECALL_KINDS, Settings
from .store import Store

log = logging.getLogger("liessure")
STATIC = Path(__file__).parent / "static"
PER_CARD_TIMEOUT = 8.0


def pick_kinds(weights: dict[str, int], available: dict[str, bool], n: int, rng: random.Random) -> list[str]:
    pool = [k for k in KINDS if available.get(k) and weights.get(k, 0) > 0]
    if not pool:
        return []
    picked: list[str] = []
    for _ in range(n):
        capped = [k for k in pool if picked.count(k) < MAX_PER_LOAD.get(k, n)]
        if not capped:
            break
        choices = [k for k in capped if not picked or k != picked[-1]] or capped
        picked.append(rng.choices(choices, weights=[weights[k] for k in choices])[0])
    return picked


class Idea(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    about: str = Field("", max_length=400)


class IdeaStatus(BaseModel):
    status: str = Field(pattern="^(new|planned|done|dropped)$")


class Event(BaseModel):
    kind: str
    action: str = Field(pattern="^(open|play)$")
    ref: str = Field("", max_length=128)
    title: str = Field("", max_length=300)
    # What the card said, kept for recall questions (RECALL_KINDS only).
    subtitle: str = Field("", max_length=300)
    text: str = Field("", max_length=4000)
    url: str = Field("", max_length=1000)


class RecallAnswer(BaseModel):
    correct: bool


class MathHelp(BaseModel):
    question: str = Field(max_length=400)
    answer: str = Field(max_length=40)
    theirs: str = Field(max_length=40)
    solution: str = Field("", max_length=600)


class MathAnswer(BaseModel):
    ref: str = Field(max_length=64)
    topic: str = Field("", max_length=40)
    correct: bool
    tries: int = Field(1, ge=0, le=99)
    revealed: bool = False


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    store: Store | None = None,
    run_curator: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    store = store or Store(f"{settings.data_dir}/liessure.db")
    profiles = profile.ProfileCache(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.client = httpx.AsyncClient(
            timeout=httpx.Timeout(6.0, connect=3.0),
            headers={"User-Agent": settings.user_agent},
            follow_redirects=True,
            transport=transport,
        )
        task = None
        if run_curator and settings.curator_enabled:
            task = asyncio.create_task(curator.loop(app.state.client, settings, store, profiles))
        try:
            yield
        finally:
            if task:
                task.cancel()
            await app.state.client.aclose()

    app = FastAPI(title="Liessure", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.store = store
    app.state.profiles = profiles

    async def one(client: httpx.AsyncClient, kind: str, rng: random.Random) -> dict | None:
        if kind == "recall":
            row = store.due_recall()
            return sources.recall_card(row).to_dict() if row else None
        if kind == "todo":
            return await todo_card(client, rng)
        # Mostly the curated pool; a share of uncurated cards keeps some surprise in it.
        if settings.curator_enabled and rng.random() < settings.curator_share:
            pick = store.take(kind, rng.random())
            if pick:
                card = {**pick["card"], "why": pick["reason"]}
                if any("forvo.com/" in a.get("url", "") for a in card.get("actions") or []):
                    # Phrase cards curated with Forvo links, which don't work for phrases: speak them instead.
                    card["actions"] = curator.phrase_actions(card["title"], curator.code_for_label(settings, card["label"]))
                    card["url"] = curator.phrase_url(card["title"])
                return card
        try:
            s = await profiles.get(client)  # the graph's languages, home and interests, or the env fallback
            card = await asyncio.wait_for(sources.SOURCES[kind](client, s, rng), PER_CARD_TIMEOUT)
            return card.to_dict()
        except (sources.Unavailable, httpx.HTTPError, asyncio.TimeoutError, KeyError, ValueError, TypeError) as e:
            log.warning("source %s failed: %s: %s", kind, type(e).__name__, e)
            return None

    async def todo_card(client: httpx.AsyncClient, rng: random.Random) -> dict | None:
        """An open to-do item that fits the moment, with how long it takes."""
        try:
            items = await sources.todo_items(client, settings)
            if not items:
                return None
            await curator.estimate_todos(client, settings, store, items)  # only new or changed items
        except (httpx.HTTPError, sources.Unavailable, ValueError) as e:
            log.warning("todo failed: %s: %s", type(e).__name__, e)
            return None
        known = store.todo_estimates()
        hour = datetime.now().hour
        ready = [(i, known[i["id"]]) for i in items
                 if i["id"] in known and known[i["id"]]["text_hash"] == sources.todo_text_hash(i)]
        if not ready:
            return None
        item, est = rng.choices(ready, weights=[sources.todo_fits(e["minutes"], hour) for _, e in ready])[0]
        return sources.todo_card(settings, item, est).to_dict()

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/api/sources")
    async def list_sources(request: Request):
        avail = sources.configured(settings)
        await profiles.get(request.app.state.client)
        return {
            "language": "Languages",
            "profile": "graph" if profiles.raw is not None else "env",
            "curator": {
                "enabled": settings.curator_enabled,
                "pools": {k: store.pool_size(k) for k in curator.enabled_kinds(settings)}
                if settings.curator_enabled else {},
            },
            "sources": {k: {"configured": avail[k], "weight": settings.weights.get(k, 0)} for k in KINDS},
        }

    @app.post("/api/event", status_code=204)
    async def event(e: Event):
        """What you opened or played. Feeds the curator's 'recommended next' seeds."""
        if e.kind not in KINDS:
            raise HTTPException(400, "unknown kind")
        store.record(e.kind, e.ref, e.action, e.title)
        if e.kind in RECALL_KINDS and e.ref and e.title and e.url.startswith("https://"):
            store.remember_card(e.kind, e.ref, e.title, e.subtitle, e.text, e.url)
        return Response(status_code=204)

    @app.post("/api/recall/{recall_id}")
    async def answer_recall(recall_id: int, a: RecallAnswer):
        """You answered a recall question: right moves it to a longer interval, wrong starts it over."""
        out = store.answer_recall(recall_id, a.correct)
        if out is None:
            raise HTTPException(404, "no such question")
        return out

    @app.post("/api/math")
    async def answer_math(a: MathAnswer):
        """A math card answered (or given up on): kept for progress, returns it."""
        store.record_math(a.ref, a.topic, a.correct, a.tries, a.revealed)
        return store.math_stats()

    @app.post("/api/math/help")
    async def math_help(request: Request, h: MathHelp):
        """Step-by-step help with a wrong answer: the likely mistake, then the way through."""
        if not settings.curator_enabled:
            raise HTTPException(409, "needs LLM_URL and LLM_KEY")
        try:
            text = await curator.math_help(request.app.state.client, settings, h.question, h.answer, h.theirs,
                                           h.solution)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"the LLM didn't answer: {type(e).__name__}")
        return {"help": text[:2000]}

    @app.get("/api/math/stats")
    async def math_stats():
        return store.math_stats()

    @app.get("/api/recall/stats")
    async def recall_stats():
        return store.recall_stats()

    @app.post("/api/ideas", status_code=201)
    async def add_idea(i: Idea):
        """An improvement jotted down from the app's ideas button, kept to build later."""
        return {"id": store.add_idea(i.text.strip(), i.about.strip())}

    @app.get("/api/ideas")
    async def list_ideas(status: str = ""):
        ideas = store.ideas()
        return {"ideas": [i for i in ideas if not status or i["status"] == status]}

    @app.post("/api/ideas/{idea_id}/status")
    async def idea_status(idea_id: int, body: IdeaStatus):
        if not store.set_idea_status(idea_id, body.status):
            raise HTTPException(404, "no such idea")
        return {"id": idea_id, "status": body.status}

    @app.post("/api/curate")
    async def curate_now(request: Request, kind: str = ""):
        """Run one curation pass now (also runs on its own schedule). ?kind= limits it to one kind."""
        if not settings.curator_enabled:
            raise HTTPException(409, "curator needs LLM_URL and LLM_KEY")
        only = None
        if kind:
            if kind not in curator.enabled_kinds(settings):
                raise HTTPException(400, f"{kind} is not curated here")
            only = [kind]
        return await curator.curate(request.app.state.client, settings, store, only=only, profiles=profiles)

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

    @app.get("/stream/audio/{item_id}")
    async def stream_audio(request: Request, item_id: str):
        """Jellyfin audio, proxied so the page needs no Jellyfin token. Passes Range through for seeking."""
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", item_id) or not (settings.jellyfin.url and settings.jellyfin_token):
            raise HTTPException(404)
        params = {
            "Container": "mp3,aac,m4a|aac,flac,ogg,opus,webm|opus",
            "TranscodingContainer": "mp3", "TranscodingProtocol": "http", "AudioCodec": "mp3",
            "MaxStreamingBitrate": "320000",
        }
        if settings.jellyfin_user_id:
            params["UserId"] = settings.jellyfin_user_id
        headers = sources.auth_headers_for(settings, settings.jellyfin.url + "/") or {}
        if "range" in request.headers:
            headers = {**headers, "Range": request.headers["range"]}
        client: httpx.AsyncClient = request.app.state.client
        upstream = await client.send(
            client.build_request("GET", f"{settings.jellyfin.url}/Audio/{item_id}/universal", params=params, headers=headers),
            stream=True,
        )
        if upstream.status_code not in (200, 206):
            await upstream.aclose()
            raise HTTPException(502, "jellyfin refused the stream")
        passthrough = {k: v for k, v in upstream.headers.items()
                       if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")}
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=passthrough,
                                 background=BackgroundTask(upstream.aclose))

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=STATIC), name="static")
    return app

