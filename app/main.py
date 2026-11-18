from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import sqlite3
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel, Field

import dataclasses

from . import auth, avoid, curator, exercise, flashcards, lessons, lyrics, profile, reader, recommend, sources
from .config import LESSON_LABELS
from .config import KINDS, MAX_PER_LOAD, OWNER_ONLY_KINDS, RECALL_KINDS, Settings
from .store import Store

log = logging.getLogger("liessure")
STATIC = Path(__file__).parent / "static"
PER_CARD_TIMEOUT = 8.0


class FreshStaticFiles(StaticFiles):
    """The page's code always revalidates. Without a Cache-Control header the WebView guessed a freshness
    from Last-Modified and kept running an old app.js for hours after a deploy."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


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


def spread(cards: list[dict]) -> list[dict]:
    """The same cards, in order as far as possible, but never two of a kind side by side when that can be
    avoided (a card that came back empty can leave two songs touching)."""
    rest, out = list(cards), []
    while rest:
        i = next((j for j, c in enumerate(rest) if not out or c["kind"] != out[-1]["kind"]), 0)
        out.append(rest.pop(i))
    return out


def engagement_weights(weights: dict[str, int], stats: dict[str, dict[str, int]]) -> dict[str, float]:
    """Kind weights nudged by what this person engages with: a kind they open or linger on comes up more,
    one they scroll past less (0.5x to 1.8x), once there are at least 6 cards of it to judge by."""
    judged = {k: v for k, v in stats.items() if v["seen"] >= 6}
    if not judged:
        return dict(weights)
    overall = sum(v["engaged"] for v in judged.values()) / max(1, sum(v["seen"] for v in judged.values()))
    out: dict[str, float] = {}
    for k, w in weights.items():
        v = judged.get(k)
        if not v or not w:
            out[k] = w
            continue
        rate = (v["engaged"] + 1) / (v["seen"] + 2) - 0.5 * v["skipped"] / v["seen"] * (1 - overall)
        out[k] = w * min(1.8, max(0.5, rate / max(0.05, overall)))
    return out


class ViewEvent(BaseModel):
    kind: str = Field(max_length=40)
    ref: str = Field("", max_length=128)
    title: str = Field("", max_length=300)
    seconds: float = Field(ge=0, le=3600)


class CardState(BaseModel):
    card: dict = Field(...)
    state: str = Field(pattern="^(done|later|)$")


class DeckIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class FlashIn(BaseModel):
    front: str = Field(min_length=1, max_length=2000)
    back: str = Field(min_length=1, max_length=4000)


class FromCard(BaseModel):
    card: dict = Field(...)


class CardToTodo(BaseModel):
    card: dict = Field(...)
    why: str = Field(pattern="^(study|implement)$")


class GetIt(BaseModel):
    media: str = Field(pattern="^(movie|tv)$")
    tmdb: int = Field(gt=0)
    title: str = Field("", max_length=300)


class ExerciseDone(BaseModel):
    id: str = Field(max_length=80)
    name: str = Field("", max_length=200)
    area: str = Field("", max_length=60)


class ListenTo(BaseModel):
    card: dict = Field(...)


class RouteFor(BaseModel):
    card: dict = Field(...)


class Grade(BaseModel):
    good: bool


class AvoidTerm(BaseModel):
    term: str = Field(min_length=1, max_length=60)


class Idea(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    about: str = Field("", max_length=400)


class IdeaStatus(BaseModel):
    status: str = Field(pattern="^(new|planned|done|dropped)$")


class Event(BaseModel):
    kind: str
    action: str = Field(pattern="^(open|play|read)$")  # read: expanded and read in the feed
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


class MathAssist(BaseModel):
    ref: str = Field(max_length=64)
    topic: str = Field("", max_length=40)
    kind: str = Field(pattern="^(hint|fix|tutor)$")


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
    sso = auth.SSO(settings, store) if settings.sso_enabled else None
    app.state.sso = sso

    # Public without a session: health, sign-in itself, and the page's static code (no data in it).
    PUBLIC = ("/healthz", "/auth/", "/app.js", "/style.css", "/sw.js", "/icon.svg", "/manifest.webmanifest")

    @app.middleware("http")
    async def who(request: Request, call_next):
        if sso is None:
            request.state.user, request.state.name = auth.OWNER, ""
            return await call_next(request)
        path = request.url.path
        sess = sso.session(request.cookies.get(auth.COOKIE))
        if sess is None and not any(path == p or (p.endswith("/") and path.startswith(p)) for p in PUBLIC):
            if path == "/" or request.headers.get("accept", "").startswith("text/html"):
                return RedirectResponse("/auth/login", status_code=303)
            return JSONResponse({"detail": "sign in first"}, status_code=401)
        request.state.user = sess["user"] if sess else ""
        request.state.name = sess["name"] if sess else ""
        return await call_next(request)

    @app.get("/auth/login")
    async def login(request: Request, app: str = ""):
        if sso is None:
            return RedirectResponse("/", status_code=303)
        try:
            url = await sso.login_url(request.app.state.client, app_challenge=app)
        except ValueError:
            raise HTTPException(400, "bad app challenge")
        return RedirectResponse(url, status_code=303)

    @app.get("/auth/ticket")
    async def ticket(request: Request, ticket: str = "", verifier: str = ""):
        """The Android app finishing a sign-in it did in a browser tab (see auth.APP_RETURN)."""
        if sso is None:
            return RedirectResponse("/", status_code=303)
        try:
            token = sso.redeem(ticket, verifier)
        except (ValueError, PermissionError) as e:
            return Response(f"Not signed in: {e}", status_code=403, media_type="text/plain")
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_TTL, httponly=True, secure=True, samesite="lax")
        return resp

    @app.get("/auth/callback")
    async def callback(request: Request):
        if sso is None:
            return RedirectResponse("/", status_code=303)
        try:
            token, _ = await sso.callback(request.app.state.client, dict(request.query_params))
        except PermissionError as e:
            return Response(f"Not signed in: {e}", status_code=403, media_type="text/plain")
        except (ValueError, httpx.HTTPError, KeyError) as e:
            log.warning("sign-in failed: %s: %s", type(e).__name__, e)
            return Response("Sign-in didn't complete. <a href=/auth/login>Try again</a>.", status_code=400,
                            media_type="text/html")
        if token.startswith("app:"):
            # Back to the app with a one-time ticket; the browser tab never gets a session.
            return RedirectResponse(f"{auth.APP_RETURN}?ticket={token[4:]}", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_TTL, httponly=True, secure=True, samesite="lax")
        return resp

    @app.get("/auth/logout")
    async def logout(request: Request):
        if sso is not None:
            sso.logout(request.cookies.get(auth.COOKIE))
        resp = RedirectResponse("/auth/login" if sso else "/", status_code=303)
        resp.delete_cookie(auth.COOKIE)
        return resp

    @app.get("/api/me/avoid")
    async def avoid_list(request: Request):
        return {"avoid": store.avoid_list(request.state.user), "suggested": avoid.SUGGESTED}

    @app.post("/api/me/avoid")
    async def avoid_add(request: Request, t: AvoidTerm):
        """Never show this topic again: a category (sports, alcohol, animal products) or any word."""
        store.add_avoid(t.term, request.state.user)
        return {"avoid": store.avoid_list(request.state.user)}

    @app.delete("/api/me/avoid/{term}")
    async def avoid_remove(request: Request, term: str):
        store.remove_avoid(term, request.state.user)
        return {"avoid": store.avoid_list(request.state.user)}

    @app.get("/api/me")
    async def me(request: Request):
        user = request.state.user
        return {"user": user, "name": request.state.name, "owner": user == auth.OWNER,
                "sso": sso is not None, "lang": prefs_for(user).get("lang", "en"),
                "todo": user == auth.OWNER and bool(settings.lists.url and settings.lists_token)}

    def prefs_for(user: str) -> dict:
        return settings.user_prefs.get(settings.owner_sub if user == auth.OWNER else user, {}) or {}

    def settings_for(user: str) -> Settings:
        """The settings a person's cards are made with: their reading language, news, interests and lessons."""
        p = prefs_for(user)
        if not p:
            return settings
        changes: dict = {}
        if p.get("wikipedia"):
            changes["wikipedia_lang"] = p["wikipedia"]
        if p.get("news_feeds"):
            changes["news_feeds"] = list(p["news_feeds"])
        if p.get("interests"):
            changes["wiki_interests"] = list(p["interests"])
        if p.get("video_topics"):
            changes["invidious_topics"] = list(p["video_topics"])
        if p.get("learning"):
            changes["languages_learning"] = [(x["name"], x["code"]) for x in p["learning"]]
            changes["languages_curious"] = []
        if p.get("lang_name") and p.get("lang"):
            changes["languages_fluent"] = [(p["lang_name"], p["lang"])]
            if p["lang"] != "en":
                changes["reply_lang"] = p["lang_name"]
        if p.get("learning"):
            # Lessons are their way into a new language; a whole Wikipedia article in it is a wall of text.
            changes["language_wiki"] = False
        if user != auth.OWNER:
            # The owner's own practice links (drill app, Lingopie) aren't theirs.
            changes["language_practice_url"] = ""
            changes["lingopie_url"] = ""
        return dataclasses.replace(settings, **changes)

    def lesson_prefs(user: str, s: Settings) -> dict:
        """Their lesson preferences, or beginner lessons (explained in English) in the languages their
        profile says they're learning."""
        p = prefs_for(user)
        if p.get("learning"):
            return p
        return {**p, "lang": p.get("lang", "en"), "lang_name": p.get("lang_name", "English"),
                "learning": [{"name": n, "code": c, "style": "beginner"} for n, c in s.languages_learning]}

    def learns_korean(user: str) -> bool:
        codes = [x.get("code") for x in prefs_for(user).get("learning") or []]
        if user == auth.OWNER and not codes:
            codes = [c for _, c in settings.languages_learning]
        return "ko" in codes

    def kinds_for(user: str) -> set[str]:
        """The owner sees everything; others don't see cards built from the owner's own services,
        nor kinds their preferences turn off. Lessons are for people with something to learn."""
        p = prefs_for(user)
        kinds = set(KINDS) if user == auth.OWNER else set(KINDS) - OWNER_ONLY_KINDS
        kinds -= set(p.get("kinds_off") or [])
        if not (p.get("learning") or (user == auth.OWNER and settings.languages_learning)):
            kinds.discard("lesson")
        if not p.get("career"):
            kinds.discard("career")
        if not learns_korean(user):
            kinds.discard("kquiz")
        if not store.decks(user):
            kinds.discard("flashcard")
        return kinds

    async def one(client: httpx.AsyncClient, kind: str, rng: random.Random, user: str = auth.OWNER) -> dict | None:
        """A card, skipping (up to three tries) anything on the reader's not-interested list."""
        terms = store.avoid_list(user)
        done = store.done_keys(user)
        for _ in range(3):
            card = await _one(client, kind, rng, user)
            if card is None or (not avoid.matches(terms, card) and store.card_key(card) not in done):
                return card
        return None

    async def _one(client: httpx.AsyncClient, kind: str, rng: random.Random, user: str = auth.OWNER) -> dict | None:
        if kind not in kinds_for(user):
            return None
        if kind == "recall":
            row = store.due_recall(user)
            return sources.recall_card(row).to_dict() if row else None
        if kind == "career":
            return await own_career(client, user, rng)
        if kind == "flashcard":
            row = store.next_flashcard(user)
            return flashcards.card_for(row, LESSON_LABELS.get(prefs_for(user).get("lang", ""), {})) if row else None
        if kind == "lesson":
            s = await profiles.get(client) if user == auth.OWNER else settings_for(user)
            return await lessons.card(client, s, store, user, lesson_prefs(user, s), rng)
        if kind == "bite":
            if user == auth.OWNER:
                pick = store.take("bite", rng.random())
                return pick["card"] if pick else None
            return await own_bite(client, user, rng)
        if kind == "todo":
            return await todo_card(client, rng)
        if kind == "exercise":
            return await exercise_card(client, rng, user)
        if kind == "recommend":
            try:
                return (await recommend.card(client, settings, rng, store.shown_refs(user, "recommend"))).to_dict()
            except (sources.Unavailable, httpx.HTTPError, ValueError, KeyError) as e:
                log.info("recommend: %s: %s", type(e).__name__, e)
                return None
        # Mostly the curated pool; a share of uncurated cards keeps some surprise in it.
        # The curated pool is picked for the owner's profile; others get fresh cards until per-user curation.
        if user == auth.OWNER and settings.curator_enabled and rng.random() < settings.curator_share:
            pick = store.take(kind, rng.random())
            if pick:
                card = {**pick["card"], "why": pick["reason"]}
                if any("forvo.com/" in a.get("url", "") for a in card.get("actions") or []):
                    # Phrase cards curated with Forvo links, which don't work for phrases: speak them instead.
                    card["actions"] = curator.phrase_actions(card["title"], curator.code_for_label(settings, card["label"]))
                    card["url"] = curator.phrase_url(card["title"])
                return card
        try:
            # The owner's profile comes from the household graph; others from their preferences (for now).
            s = await profiles.get(client) if user == auth.OWNER else settings_for(user)
            card = await asyncio.wait_for(sources.SOURCES[kind](client, s, rng), PER_CARD_TIMEOUT)
            return card.to_dict()
        except (sources.Unavailable, httpx.HTTPError, asyncio.TimeoutError, KeyError, ValueError, TypeError) as e:
            log.warning("source %s failed: %s: %s", kind, type(e).__name__, e)
            return None

    async def own_bite(client: httpx.AsyncClient, user: str, rng: random.Random) -> dict | None:
        """Bite-size cards from this person's interests, in their language, in their own pool."""
        pool = f"bite:{user}"
        pick = store.take(pool, rng.random())
        if not pick and settings.curator_enabled:
            s = settings_for(user)
            try:
                await curator.generate_bites(client, s, store, rng, topics=1, pool=pool,
                                             write_in=prefs_for(user).get("lang_name", ""))
            except httpx.HTTPError as e:
                log.warning("bites for %s: %s", user, e)
            pick = store.take(pool, rng.random())
        return pick["card"] if pick else None

    async def own_career(client: httpx.AsyncClient, user: str, rng: random.Random) -> dict | None:
        """Careers this person is exploring, in their language, from their own pool."""
        pool = f"career:{user}"
        pick = store.take(pool, rng.random())
        if not pick and settings.curator_enabled:
            try:
                await curator.generate_career_cards(client, settings_for(user), store, rng, pool,
                                                    prefs_for(user)["career"], prefs_for(user).get("lang_name", ""))
            except (httpx.HTTPError, KeyError) as e:
                log.warning("careers for %s: %s", user, e)
            pick = store.take(pool, rng.random())
        return pick["card"] if pick else None

    def day_start(now: datetime) -> float:
        return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    async def exercise_card(client: httpx.AsyncClient, rng: random.Random, user: str,
                            now: datetime | None = None) -> dict | None:
        """A short exercise that fits the hour; none while Not now holds, no cardio after a run today."""
        now = now or datetime.now().astimezone()
        if float(store.get_meta(f"exercise_snooze:{user}") or 0) > now.timestamp():
            return None
        done = {r["ex_id"] for r in store.exercises_since(user, day_start(now))}
        mine: list[dict] = []
        if settings.lists.url and settings.lists_token and settings.exercise_list_id:
            try:
                r = await client.get(f"{settings.lists.url}/lists/{settings.exercise_list_id}",
                                     params={"include_checked": "false"},
                                     headers={"Authorization": f"Bearer {settings.lists_token}"}, timeout=10)
                r.raise_for_status()
                mine = exercise.from_list(r.json().get("items") or [])
            except (httpx.HTTPError, ValueError) as e:
                log.info("exercises list: %s", e)
        ran = False
        try:
            ran = await exercise.ran_today(client, settings, now)
        except (httpx.HTTPError, ValueError) as e:
            log.info("activity tracker: %s", e)
        try:
            pick = exercise.choose(rng, now, done, ran, mine)
        except sources.Unavailable:
            return None
        return exercise.card_for(pick, now, ran).to_dict()

    @app.post("/api/recommend/get")
    async def get_it(request: Request, body: GetIt):
        """Get it: a Jellyseerr request for this movie or series (the owner's; it downloads to the shared library)."""
        if request.state.user != auth.OWNER:
            raise HTTPException(403, "requests are the owner's")
        if not (settings.jellyseerr_url and settings.jellyseerr_api_key):
            raise HTTPException(409, "Jellyseerr isn't configured")
        try:
            out = await recommend.request(request.app.state.client, settings, body.media, body.tmdb)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Jellyseerr didn't take it: {type(e).__name__}")
        store.record("recommend", f"rec:{body.media}:{body.tmdb}", "get", body.title, user=request.state.user)
        return out

    @app.post("/api/exercise/done")
    async def exercise_done(request: Request, body: ExerciseDone):
        """Done on an exercise card: kept, so it isn't offered again today. The reply is today's tally."""
        user = request.state.user
        store.log_exercise(user, body.id, body.name, body.area)
        today = store.exercises_since(user, day_start(datetime.now().astimezone()))
        return {"today": len(today), "minutes": sum(next((e["minutes"] for e in exercise.LIBRARY
                                                          if e["id"] == r["ex_id"]), 5) for r in today)}

    @app.post("/api/exercise/not-now")
    async def exercise_not_now(request: Request, hours: float = Query(2, ge=0.5, le=24)):
        """Not now: no exercise cards for a while."""
        until = datetime.now().timestamp() + hours * 3600
        store.set_meta(f"exercise_snooze:{request.state.user}", str(until))
        return {"until": until}

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
        mine = kinds_for(request.state.user)
        avail = {k: v and k in mine for k, v in sources.configured(settings).items()}
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

    @app.post("/api/views", status_code=204)
    async def views(request: Request, body: list[ViewEvent]):
        """How long cards were on screen (batched by the page): lingering and scrolling past, for what to show."""
        user = request.state.user
        for v in body[:60]:
            if v.kind in KINDS and v.ref:
                store.record(v.kind, v.ref, "view", v.title, user=user, value=round(v.seconds, 1))
        return Response(status_code=204)

    @app.get("/api/engagement")
    async def engagement(request: Request, days: float = Query(30, ge=1, le=365)):
        """Per kind over the last `days`: cards seen, engaged with, scrolled past, and the weight it now gets."""
        user = request.state.user
        stats = store.engagement(user, days)
        weights = engagement_weights(settings.weights, store.engagement(user))
        return {"kinds": {k: {**v, "weight": round(weights.get(k, 0), 2), "base": settings.weights.get(k, 0)}
                          for k, v in sorted(stats.items())}}

    @app.post("/api/event", status_code=204)
    async def event(request: Request, e: Event):
        """What you opened or played. Feeds the curator's 'recommended next' seeds."""
        if e.kind not in KINDS:
            raise HTTPException(400, "unknown kind")
        user = request.state.user
        store.record(e.kind, e.ref, e.action, e.title, user=user)
        if e.kind in RECALL_KINDS and e.ref and e.title and e.url.startswith("https://"):
            store.remember_card(e.kind, e.ref, e.title, e.subtitle, e.text, e.url, user=user)
        return Response(status_code=204)

    @app.post("/api/recall/{recall_id}")
    async def answer_recall(request: Request, recall_id: int, a: RecallAnswer):
        """You answered a recall question: right moves it to a longer interval, wrong starts it over."""
        out = store.answer_recall(recall_id, a.correct, request.state.user)
        if out is None:
            raise HTTPException(404, "no such question")
        return out

    @app.post("/api/math")
    async def answer_math(request: Request, a: MathAnswer):
        """A math card answered (or given up on): kept for progress, returns it."""
        store.record_math(a.ref, a.topic, a.correct, a.tries, a.revealed, user=request.state.user)
        return store.math_stats(user=request.state.user)

    _read_cache: dict[tuple[int, str], str] = {}

    @app.get("/read/phrack/{issue}/{slug}")
    async def read_phrack(request: Request, issue: int, slug: str):
        """A Phrack article reflowed for a phone (see reader.py)."""
        if not (1 <= issue <= 999 and reader.SLUG.match(slug)):
            raise HTTPException(404)
        key = (issue, slug)
        if key not in _read_cache:
            try:
                _read_cache[key] = reader.page(issue, slug, await reader.fetch_text(request.app.state.client, issue, slug))
            except (httpx.HTTPError, ValueError) as e:
                log.warning("phrack reader %s/%s: %s", issue, slug, e)
                return RedirectResponse(f"{reader.PHRACK}/issues/{issue}/{slug}.html", status_code=302)
            if len(_read_cache) > 200:
                _read_cache.pop(next(iter(_read_cache)))
        return Response(_read_cache[key], media_type="text/html", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/job/{job_id}/description")
    async def job_description(request: Request, job_id: int):
        """A job card's full description, fetched when it's expanded. The owner's only, like job cards."""
        if "job" not in kinds_for(request.state.user):
            raise HTTPException(404)
        if not (settings.jobs.url and settings.jobs_token):
            raise HTTPException(409, "jobs service not configured")
        try:
            text = await sources.job_description(request.app.state.client, settings, job_id)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"the jobs service didn't answer: {type(e).__name__}")
        return {"text": text[:60000]}

    @app.post("/api/photo/{asset_id}/identify")
    async def identify(request: Request, asset_id: str):
        """What's in a photo: the household vision model, with the photo's date and place as hints."""
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", asset_id):
            raise HTTPException(400, "not an asset id")
        if not (settings.curator_enabled and settings.immich.url and settings.immich_api_key):
            raise HTTPException(409, "needs the LLM and Immich")
        try:
            text = await curator.identify_photo(request.app.state.client, settings_for(request.state.user), asset_id)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"couldn't look at it: {type(e).__name__}")
        return {"text": text[:2000]}

    @app.post("/api/math/help")
    async def math_help(request: Request, h: MathHelp):
        """Step-by-step help with a wrong answer: the likely mistake, then the way through."""
        if not settings.curator_enabled:
            raise HTTPException(409, "needs LLM_URL and LLM_KEY")
        try:
            text = await curator.math_help(request.app.state.client, settings_for(request.state.user), h.question,
                                           h.answer, h.theirs, h.solution)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"the LLM didn't answer: {type(e).__name__}")
        return {"help": text[:2000]}

    @app.post("/api/math/assist", status_code=204)
    async def math_assist(request: Request, a: MathAssist):
        """Help used on a math problem (hint, fix-it, Claude tutor), so progress shows where help was needed."""
        store.record_math_help(a.ref, a.topic, a.kind, user=request.state.user)
        return Response(status_code=204)

    @app.get("/api/math/stats")
    async def math_stats(request: Request):
        return store.math_stats(user=request.state.user)

    @app.get("/api/recall/stats")
    async def recall_stats(request: Request):
        return store.recall_stats(request.state.user)

    @app.post("/api/ideas", status_code=201)
    async def add_idea(request: Request, i: Idea):
        """An improvement jotted down from the app's ideas button, kept to build later."""
        return {"id": store.add_idea(i.text.strip(), i.about.strip(), user=request.state.user)}

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
        user = request.state.user
        mine = kinds_for(user)
        avail = {k: v and k in mine for k, v in sources.configured(settings).items()}
        if kinds:
            wanted = {k.strip() for k in kinds.split(",")}
            avail = {k: v and k in wanted for k, v in avail.items()}
        weights = engagement_weights(settings.weights, store.engagement(user))
        plan = pick_kinds(weights, avail, n, rng)
        client = request.app.state.client
        cards = await asyncio.gather(*(one(client, k, rng, user) for k in plan))
        out = [c for c in cards if c]
        # Kinds with nothing right now (trivia still downloading, exercise on Not now): one round of others instead,
        # each placed where it doesn't sit next to a card of its own kind.
        empty = {k for k, c in zip(plan, cards) if not c}
        if empty and len(out) < len(plan):
            more_plan = pick_kinds(weights, {k: v and k not in empty for k, v in avail.items()},
                                   len(plan) - len(out), rng)
            for extra in await asyncio.gather(*(one(client, k, rng, user) for k in more_plan)):
                if not extra:
                    continue
                spots = [i for i in range(len(out) + 1)
                         if (i == 0 or out[i - 1]["kind"] != extra["kind"])
                         and (i == len(out) or out[i]["kind"] != extra["kind"])]
                if spots:
                    out.insert(rng.choice(spots), extra)
        out = spread(out)
        # Something always beats an empty screen.
        if not out and plan:
            fallback = await one(client, "wikipedia", rng, user)
            if fallback:
                out.append(fallback)
        # A 'later' card that's due comes back (one per load, at a random spot).
        again = store.due_later(user)
        if again and not kinds:
            out.insert(rng.randint(0, len(out)), {**again, "again": True})
        store.remember_shown([c for c in out if not c.get("again")], user)  # so it can be found again (/api/history)
        return {"cards": out}

    @app.get("/api/card/{kind}")
    async def card(request: Request, kind: str):
        if kind not in sources.SOURCES:
            raise HTTPException(404, "unknown kind")
        c = await one(request.app.state.client, kind, random.Random(), request.state.user)
        if not c:
            raise HTTPException(503, f"{kind} has nothing right now")
        return c

    @app.post("/api/card-state")
    async def card_state(request: Request, s: CardState):
        """Swiped: 'done' (off the feed, onto the Done list), 'later' (the Later list, and back in the feed on a
        spaced schedule), or '' to put it back to normal."""
        user = request.state.user
        store.set_state(s.card, s.state, user)
        if s.state and s.card.get("kind") in KINDS:  # Done or Later, for engagement (Later counts as interest)
            store.record(str(s.card.get("kind")), str(s.card.get("ref") or ""), s.state,
                         str(s.card.get("title") or ""), user=user)
        # A to-do swiped to Done is checked off the To Do list (and unchecked if put back). The owner's only.
        ref = str(s.card.get("ref") or "")
        if (s.card.get("kind") == "todo" and user == auth.OWNER and ref.startswith("todo:") and ref[5:].isdigit()
                and settings.lists.url and settings.lists_token and s.state in ("done", "")):
            try:
                await sources.set_todo_checked(request.app.state.client, settings, int(ref[5:]), s.state == "done")
            except httpx.HTTPError as e:
                log.warning("todo %s: %s", ref, e)
                return {"checked": False, "error": f"the lists service didn't take it: {type(e).__name__}"}
            return {"checked": s.state == "done"}
        return Response(status_code=204)

    @app.get("/api/lists/{state}")
    async def state_list(request: Request, state: str):
        if state not in ("done", "later"):
            raise HTTPException(404)
        return {"cards": store.state_list(state, request.state.user)}

    # ---------------------------------------------------------- flashcards
    @app.get("/api/decks")
    async def decks(request: Request):
        return {"decks": store.decks(request.state.user)}

    @app.post("/api/decks")
    async def new_deck(request: Request, d: DeckIn):
        return {"id": store.add_deck(d.name, request.state.user, source="made")}

    @app.delete("/api/decks/{deck_id}")
    async def delete_deck(request: Request, deck_id: int):
        if not store.delete_deck(deck_id, request.state.user):
            raise HTTPException(404)
        return Response(status_code=204)

    @app.get("/api/decks/{deck_id}/cards")
    async def deck_cards(request: Request, deck_id: int):
        return {"cards": store.deck_cards(deck_id, request.state.user)}

    @app.post("/api/decks/{deck_id}/cards")
    async def add_card(request: Request, deck_id: int, c: FlashIn):
        try:
            store.add_flashcards(deck_id, [(c.front.strip(), c.back.strip())], request.state.user)
        except KeyError:
            raise HTTPException(404, "no such deck")
        return {"decks": store.decks(request.state.user)}

    @app.delete("/api/flashcards/{card_id}")
    async def delete_card(request: Request, card_id: int):
        if not store.delete_flashcard(card_id, request.state.user):
            raise HTTPException(404)
        return Response(status_code=204)

    @app.post("/api/decks/import")
    async def import_deck(request: Request, name: str = Query("", max_length=80),
                          filename: str = Query("", max_length=200)):
        """A CSV/TSV (front, back) or an Anki .apkg, sent as the raw request body."""
        data = await request.body()
        if not data or len(data) > 200 * 2 ** 20:
            raise HTTPException(400, "send the file as the request body (up to 200 MB)")
        try:
            cards = flashcards.parse(data, filename)
        except (ValueError, zipfile.BadZipFile, sqlite3.DatabaseError) as e:
            raise HTTPException(400, f"couldn't read that deck: {e}")
        if not cards:
            raise HTTPException(400, "no cards found (two columns: front, back)")
        deck = store.add_deck(name or re.sub(r"\.\w+$", "", filename) or "Imported", request.state.user,
                              source="anki" if data[:2] == b"PK" else "csv")
        store.add_flashcards(deck, cards, request.state.user)
        return {"id": deck, "cards": len(cards)}

    @app.post("/api/decks/from-card")
    async def flashcards_from_card(request: Request, body: FromCard):
        """The LLM's flashcards from one feed card's text, into the person's "From my feed" deck."""
        if not settings.curator_enabled:
            raise HTTPException(409, "needs the LLM")
        user = request.state.user
        c = body.card
        text = " ".join(str(c.get(k) or "") for k in ("subtitle", "blurb"))
        try:
            made = await curator.flashcards_from(request.app.state.client, settings_for(user), str(c.get("title", "")), text)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"the LLM didn't answer: {type(e).__name__}")
        if not made:
            raise HTTPException(422, "nothing to make cards from")
        name = "Desde mi feed" if prefs_for(user).get("lang") == "es" else "From my feed"
        deck = store.deck_by_name(name, user) or store.add_deck(name, user, source="feed")
        store.add_flashcards(deck, made, user)
        return {"deck": deck, "made": [{"front": f, "back": b} for f, b in made]}

    @app.post("/api/todo/from-card")
    async def todo_from_card(request: Request, body: CardToTodo):
        """A card onto the household To Do list, to study further or to implement. The owner's list, so the owner only."""
        if request.state.user != auth.OWNER:
            raise HTTPException(403, "the To Do list is the owner's")
        if not (settings.lists.url and settings.lists_token):
            raise HTTPException(409, "the lists service isn't configured")
        if not str(body.card.get("title") or "").strip():
            raise HTTPException(422, "the card has no title")
        try:
            item = await sources.add_todo(request.app.state.client, settings, body.card, body.why)
        except httpx.HTTPError as e:
            log.warning("todo from card: %s", e)
            raise HTTPException(502, f"the lists service didn't take it: {type(e).__name__}")
        return {"id": item.get("id"), "text": item.get("text")}

    @app.post("/api/listen")
    async def listen(request: Request, body: ListenTo):
        """What to read aloud for a card: a Wikipedia article's opening, a Phrack article's prose, or else the
        card's own words. Spoken by the phone (Android's text-to-speech, or the browser's voices)."""
        c = body.card
        title = " ".join(str(c.get("title") or "").split())[:300]
        url = str(c.get("url") or "")
        own = " ".join(" ".join(str(c.get(k) or "").split()) for k in ("subtitle", "blurb") if c.get(k))
        lang = "en"
        text = ""
        client = request.app.state.client
        try:
            m = re.match(r"https://([a-z-]+)\.(?:m\.)?wikipedia\.org/wiki/", url)
            if m:
                lang = m.group(1)
                text = await curator.wikipedia_opening(client, url, chars=6000)
            m = re.search(r"/read/phrack/(\d+)/([\w.-]+)$", url)
            if m:
                _, parts = reader.blocks(await reader.fetch_text(client, int(m.group(1)), m.group(2)))
                text = "\n\n".join(t for kind, t in parts if kind != "pre")[:20000]  # code isn't for listening
        except (httpx.HTTPError, ValueError) as e:
            log.info("listen %r: %s", title, e)
        text = text or own
        if not text.strip() and not title:
            raise HTTPException(422, "nothing to read")
        return {"text": f"{title}.\n\n{text}".strip(), "lang": lang}

    @app.post("/api/geo/route")
    async def geo_route(request: Request, body: RouteFor):
        """The line a card's text describes ("the pipeline runs from A to B") as map points: the LLM names the
        places, Nominatim finds them. Straight lines between them, so approximate. Cached per card text."""
        c = body.card
        title = str(c.get("title") or "")[:300]
        text = " ".join(str(c.get(k) or "") for k in ("subtitle", "blurb"))[:3000]
        if not settings.curator_enabled or not text.strip():
            return {"points": []}
        key = "route:" + hashlib.sha256(f"{title}\n{text}".encode()).hexdigest()[:32]
        hit = store.get_meta(key)
        if hit is not None:
            return {"points": json.loads(hit)}
        near = c.get("geo") if isinstance(c.get("geo"), dict) else None
        try:
            points = await curator.route_for(request.app.state.client, settings, title, text, near)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("route for %r: %s", title, e)
            return {"points": []}
        store.set_meta(key, json.dumps(points))
        return {"points": points}

    @app.get("/api/flashcards/next")
    async def next_flashcard(request: Request, decks: str = "", skip: str = ""):
        """Flashcard-only mode: the next due card from the chosen decks (comma-separated ids)."""
        ids = [int(x) for x in decks.split(",") if x.strip().isdigit()][:50]
        skips = {int(x) for x in skip.split(",") if x.strip().isdigit()}
        row = store.next_flashcard(request.state.user, ids or None, skips or None)
        if not row:
            return {"card": None}
        labels = LESSON_LABELS.get(prefs_for(request.state.user).get("lang", ""), {})
        return {"card": flashcards.card_for(row, labels)}

    @app.post("/api/flashcards/{card_id}/grade")
    async def grade(request: Request, card_id: int, g: Grade):
        out = store.grade_flashcard(card_id, g.good, request.state.user)
        if out is None:
            raise HTTPException(404)
        return out

    @app.get("/api/history")
    async def history(request: Request, q: str = Query("", max_length=200), limit: int = Query(40, ge=1, le=100)):
        """Cards the feed showed you, newest first; q narrows to those with every word in it."""
        return {"cards": store.search_history(q, request.state.user, limit)}

    @app.get("/img")
    async def img(request: Request, src: str):
        headers = sources.auth_headers_for(settings, src)
        if headers is None or ".." in src:
            raise HTTPException(400, "not an allowed image source")
        # Immich has no preview for some photos (a failed thumbnail job, odd formats): try the small thumbnail.
        tries = [src]
        if "/thumbnail?size=preview" in src:
            tries.append(src.replace("size=preview", "size=thumbnail"))
        for url in tries:
            try:
                r = await request.app.state.client.get(url, headers=headers)
            except httpx.HTTPError as e:
                log.warning("img %s: %s", url[:160], type(e).__name__)
                continue
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image/"):
                return Response(r.content, media_type=ctype, headers={"Cache-Control": "private, max-age=86400"})
            log.warning("img %s: HTTP %s %s", url[:160], r.status_code, ctype)
        raise HTTPException(502, "image fetch failed")

    lyrics_misses = sources._TTLCache(6 * 3600)  # songs nobody has lyrics for: not asked again for a while

    @app.get("/api/lyrics/{item_id}")
    async def song_lyrics(request: Request, item_id: str):
        """A song's lyrics (Jellyfin's, else LRCLIB's), timed when synced. Kept once found."""
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", item_id) or not (settings.jellyfin.url and settings.jellyfin_token):
            raise HTTPException(404)
        hit = store.get_meta("lyrics:" + item_id)
        if hit:
            return json.loads(hit)
        if lyrics_misses.get(item_id):
            return lyrics_misses.get(item_id)
        try:
            found = await lyrics.lyrics(request.app.state.client, settings, item_id)
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"couldn't look the lyrics up: {type(e).__name__}")
        if found["lines"]:
            store.set_meta("lyrics:" + item_id, json.dumps(found))
        else:
            lyrics_misses.put(item_id, found)
        return found

    @app.get("/stream/audio/{item_id}")
    async def stream_audio(request: Request, item_id: str):
        """Jellyfin audio, proxied so the page needs no Jellyfin token. Passes Range through for seeking."""
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", item_id) or not (settings.jellyfin.url and settings.jellyfin_token):
            raise HTTPException(404)
        headers = sources.auth_headers_for(settings, settings.jellyfin.url + "/") or {}
        client: httpx.AsyncClient = request.app.state.client
        # The original file when the browser can play it: it has a length, so the player knows the
        # duration and can seek. A live transcode has neither (the bar grew as it played).
        if await sources.browser_plays(client, settings, item_id):
            url, params = f"{settings.jellyfin.url}/Audio/{item_id}/stream", {"static": "true"}
        else:
            url, params = f"{settings.jellyfin.url}/Audio/{item_id}/universal", {
                "Container": "mp3", "TranscodingContainer": "mp3", "TranscodingProtocol": "http",
                "AudioCodec": "mp3", "MaxStreamingBitrate": "320000"}
            if settings.jellyfin_user_id:
                params["UserId"] = settings.jellyfin_user_id
        if "range" in request.headers:
            headers = {**headers, "Range": request.headers["range"]}
        upstream = await client.send(client.build_request("GET", url, params=params, headers=headers), stream=True)
        if upstream.status_code not in (200, 206):
            await upstream.aclose()
            raise HTTPException(502, "jellyfin refused the stream")
        passthrough = {k: v for k, v in upstream.headers.items()
                       if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")}
        return StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code, headers=passthrough,
                                 background=BackgroundTask(upstream.aclose))

    # The page names its code by content hash, so a phone holding an old app.js gets the new one at once.
    def _ver(name: str) -> str:
        return hashlib.sha1((STATIC / name).read_bytes()).hexdigest()[:10]
    index_html = (STATIC / "index.html").read_text()
    for name in ("app.js", "style.css"):
        index_html = index_html.replace(f'"/{name}"', f'"/{name}?v={_ver(name)}"')

    @app.get("/")
    async def index():
        return Response(index_html, media_type="text/html", headers={"Cache-Control": "no-cache"})

    app.mount("/", FreshStaticFiles(directory=STATIC), name="static")
    return app

