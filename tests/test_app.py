import json
import random
import re
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app import context, curator, sources
from app.config import Service, Settings, parse_langs, parse_weights
from app.main import create_app, pick_kinds
from app.store import Store

JF = "http://jf.test"
IM = "http://im.test"
RM = "http://romm.test"
INV = "http://inv.test"

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<title>Test Wire</title>
<item><title>Bridge reopens</title><link>https://news.example/bridge</link>
<description>&lt;p&gt;The &lt;b&gt;bridge&lt;/b&gt; reopened.&lt;/p&gt;</description><dc:creator>Ana</dc:creator>
<pubDate>Mon, 16 Nov 2099 10:00:00 GMT</pubDate><media:content url="https://news.example/b.jpg"/></item>
<item><title>Old story</title><link>https://news.example/old</link><pubDate>Mon, 01 Jan 2001 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Daily</title>
<entry><title>Comet seen</title><link rel="alternate" href="https://atom.example/comet"/>
<summary>Bright one.</summary><updated>2099-11-16T09:00:00Z</updated></entry></feed>"""

NEBULA = """<?xml version="1.0"?><rss version="2.0"><channel><title>Nebula</title>
<item><title>Why ships float</title><link>https://nebula.tv/videos/real-engineering-ships</link>
<description>Buoyancy.</description><author>Real Engineering</author></item></channel></rss>"""


async def _agen(data):
    yield data


def wiki_page(title, n=0):
    return {"ns": 0, "title": title, "description": "d", "extract": f"{title} is long. " * 30,
            "thumbnail": {"source": f"https://upload.wikimedia.org/{n}.jpg"}}


def fake_backend(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    host, path, q = request.url.host, request.url.path, request.url.params
    if host.endswith("wikipedia.org") and path.startswith("/api/rest_v1/page/random"):
        return httpx.Response(200, json={
            "type": "standard", "title": "Lighthouse", "description": "tower",
            "extract": "A lighthouse is a tower. " * 20,
            "thumbnail": {"source": "https://upload.wikimedia.org/x.jpg"},
            "content_urls": {"mobile": {"page": f"https://{host.split('.')[0]}.m.wikipedia.org/wiki/Lighthouse"}},
        })
    if host.endswith("wikipedia.org") and path == "/w/api.php":
        gen = q["generator"]
        lang = host.split(".")[0]
        if gen == "geosearch":
            lat = q["ggscoord"].split("|")[0]
            pages = [wiki_page(f"Place at {lat}"), wiki_page(f"Museum at {lat}")]
        elif gen == "search":
            pages = [wiki_page(f"Search {q['gsrsearch']}")]
        else:
            pages = [wiki_page(f"{lang} {gen} {n}") for n in range(2)]
        return httpx.Response(200, json={"query": {"pages": pages}})
    if host == "jf.test" and (path == "/Items" or path.endswith("/Similar")):
        assert "Token=\"jft\"" in request.headers["authorization"]
        t = q.get("IncludeItemTypes", "Audio")
        item = {"Id": "abc12345", "ServerId": "srv", "Name": f"A {t}", "ProductionYear": 1999,
                "Genres": ["Drama"], "ImageTags": {"Primary": "t"}, "Album": "LP",
                "AlbumArtist": "Band", "AlbumId": "alb",
                "RemoteTrailers": [{"Url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]}
        return httpx.Response(200, json={"Items": [item]})
    if host == "jf.test" and "/Images/" in path:
        return httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})
    if host == "jf.test" and path.startswith("/Audio/"):
        if request.headers.get("range") == "bytes=0-1":
            return httpx.Response(206, content=_agen(b"ID"), headers={"content-type": "audio/mpeg", "content-range": "bytes 0-1/4"})
        return httpx.Response(200, content=_agen(b"ID3x"), headers={"content-type": "audio/mpeg"})
    if host == "im.test" and path == "/api/search/random":
        assert request.headers["x-api-key"] == "imk"
        size = json.loads(request.content)["size"]
        assets = [{"id": f"p{n}", "localDateTime": "2024-05-01T10:00:00",
                   "exifInfo": {"city": "Kyoto", "country": "Japan", "latitude": 35.0 + n, "longitude": 135.7}}
                  for n in range(min(size, 2))]
        return httpx.Response(200, json=assets)
    if host == "im.test" and path == "/api/search/metadata":
        return httpx.Response(200, json={"assets": {"items": [
            {"id": "r1", "localDateTime": "2026-10-02T10:00:00",
             "exifInfo": {"city": "Leavenworth", "latitude": 47.59, "longitude": -120.66}},
            {"id": "r2", "localDateTime": "2026-10-03T10:00:00",  # at home: ignored
             "exifInfo": {"city": "Seattle", "latitude": 47.61, "longitude": -122.33}},
        ]}})
    if host == "im.test" and path.endswith("/thumbnail"):
        return httpx.Response(200, content=b"\xff\xd8thumb", headers={"content-type": "image/jpeg"})
    if host == "im.test" and path == "/api/memories":
        return httpx.Response(200, json=[])
    if host == "romm.test" and path == "/api/roms":
        assert request.headers["authorization"].startswith("Bearer ")
        limit = int(q["limit"])
        roms = [{"id": 7 + n, "name": f"Zelda {n}", "platform_display_name": "SNES", "genres": ["Adventure"],
                 "path_cover_large": "roms/1/7/cover/big.png"} for n in range(limit)]
        return httpx.Response(200, json={"items": roms, "total": 40})
    if host == "inv.test" and path == "/api/v1/search":
        return httpx.Response(200, json=[
            {"type": "video", "videoId": "short1aaaaa", "title": "short", "lengthSeconds": 30},
            {"type": "video", "videoId": "long1aaaaaa", "title": f"A long one about {q['q']}", "author": "Ch", "lengthSeconds": 900},
        ])
    if host == "inv.test" and path.startswith("/api/v1/videos/"):
        return httpx.Response(200, json={"recommendedVideos": [
            {"videoId": "relatedvid1", "title": "Suspension bridges", "author": "Eng", "lengthSeconds": 1200}]})
    if host == "news.test" and path == "/rss":
        return httpx.Response(200, content=RSS.encode())
    if host == "news.test" and path == "/atom":
        return httpx.Response(200, content=ATOM.encode())
    if host == "news.test" and path == "/nebula":
        return httpx.Response(200, content=NEBULA.encode())
    if host == "travel.test" and path == "/api/trips":
        assert request.headers["authorization"] == "Bearer tt"
        return httpx.Response(200, json=[
            {"slug": "japan-2099", "name": "Japan", "start_date": "2099-04-01", "end_date": "2099-04-20",
             "destinations": [{"name": "Kyoto", "lat": 35.01, "lon": 135.77}, "Osaka"]},
            {"slug": "old", "name": "Old trip", "start_date": "2001-01-01", "end_date": "2001-01-05"},
        ])
    return httpx.Response(404, text=f"unexpected {url}")


def full_settings(**kw) -> Settings:
    base = dict(
        jellyfin=Service(JF, "https://jellyfin.example"), jellyfin_token="jft", jellyfin_user_id="u1",
        immich=Service(IM, "https://photos.example"), immich_api_key="imk",
        romm=Service(RM, "https://games.example"), romm_token="rt",
        invidious=Service(INV, "https://watch.example"), invidious_topics=["docs"],
        language_practice_url="https://practice.example",
        news_feeds=["http://news.test/rss", "http://news.test/atom"],
        nebula_feeds=["http://news.test/nebula"],
        travel=Service("http://travel.test"), travel_token="tt",
        wiki_interests=["linguistics"],
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def clear_caches():
    for c in (sources._news_cache, sources._nebula_cache, sources._inv_cache, sources._romm_total):
        c._data.clear()


def make_client(settings, handler=fake_backend):
    app = create_app(settings, transport=httpx.MockTransport(handler), store=Store(":memory:"), run_curator=False)
    return TestClient(app)


@pytest.fixture
def client():
    with make_client(full_settings()) as c:
        yield c


def test_every_kind_produces_a_card(client):
    for kind in sources.SOURCES:
        r = client.get(f"/api/card/{kind}")
        assert r.status_code == 200, (kind, r.text)
        card = r.json()
        assert card["kind"] == kind and card["title"] and card["url"].startswith("https://")


def test_links(client):
    assert client.get("/api/card/movie").json()["url"] == "https://jellyfin.example/web/#/details?id=abc12345&serverId=srv"
    assert client.get("/api/card/photo").json()["url"].startswith("https://photos.example/photos/p")
    g = client.get("/api/card/game").json()
    assert g["url"].startswith("https://games.example/rom/")
    assert g["actions"][0] == {"label": "Play in Argosy", "url": f"argosy://launch?romm_id={g['ref']}", "android": "1"}
    assert g["image"].startswith("/img?src=http%3A%2F%2Fromm.test%2Fassets%2Fromm%2Fresources%2Froms")


def test_video_links_follow_preference():
    s = full_settings(nebula_feeds=[])
    with make_client(s) as c:
        v = c.get("/api/card/video").json()
        assert v["url"] == "https://www.youtube.com/watch?v=long1aaaaaa"
        assert v["actions"] == [{"label": "Invidious", "url": "https://watch.example/watch?v=long1aaaaaa"}]
        assert v["embed"]["src"] == "https://watch.example/embed/long1aaaaaa?autoplay=1"
    s.video_link = "invidious"
    with make_client(s) as c:
        assert c.get("/api/card/video").json()["url"] == "https://watch.example/watch?v=long1aaaaaa"


def test_nebula_cards():
    with make_client(full_settings(invidious=Service())) as c:
        v = c.get("/api/card/video").json()
        assert v["label"] == "Nebula" and v["url"] == "https://nebula.tv/videos/real-engineering-ships"
        assert v["subtitle"] == "Real Engineering"


def test_language_cards_cover_every_learning_language(client):
    labels = {client.get("/api/card/language").json()["label"] for _ in range(60)}
    assert any(l.startswith("Telugu") for l in labels) and any(l.startswith("Mandarin") for l in labels)


def test_song_offers_music_app(client):
    song = client.get("/api/card/song").json()
    assert {"label": "Open Symfonium", "app": "app.symfonik.music.player"} in song["actions"]


def test_feed(client):
    r = client.get("/api/feed?n=10").json()
    assert len(r["cards"]) == 10
    kinds = [c["kind"] for c in r["cards"]]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))


def test_feed_filter(client):
    r = client.get("/api/feed?n=5&kinds=photo").json()
    assert {c["kind"] for c in r["cards"]} == {"photo"}


def test_image_proxy_only_household_hosts(client):
    ok = client.get("/img", params={"src": f"{JF}/Items/abc/Images/Primary"})
    assert ok.status_code == 200 and ok.content == b"img"
    assert client.get("/img", params={"src": "http://169.254.169.254/latest"}).status_code == 400
    assert client.get("/img", params={"src": f"{JF}.evil.test/x"}).status_code == 400


def test_unconfigured_sources_are_skipped():
    with make_client(Settings()) as c:
        s = c.get("/api/sources").json()["sources"]
        assert s["wikipedia"]["configured"] and not s["movie"]["configured"]
        cards = c.get("/api/feed?n=6").json()["cards"]
        assert cards and {x["kind"] for x in cards} <= {"wikipedia", "language"}


def test_weights_and_pick():
    w = parse_weights("photo=0,game=9,bogus=3,video=x")
    assert w["photo"] == 0 and w["game"] == 9 and w["video"] == 3
    picked = pick_kinds(w, {k: True for k in w}, 50, random.Random(1))
    assert "photo" not in picked


def test_parse_langs():
    assert parse_langs("Telugu:te| Mandarin:zh |Tagalog:") == [("Telugu", "te"), ("Mandarin", "zh"), ("Tagalog", "")]


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Liessure" in r.text
    assert client.get("/app.js").status_code == 200


def test_inline_embeds(client):
    song = client.get("/api/card/song").json()
    assert song["embed"] == {"type": "audio", "label": "Play here", "src": "/stream/audio/abc12345"}
    movie = client.get("/api/card/movie").json()
    assert movie["embed"]["src"] == "https://watch.example/embed/dQw4w9WgXcQ?autoplay=1"


def test_audio_stream_proxy(client):
    r = client.get("/stream/audio/abcdef12")
    assert r.status_code == 200 and r.content == b"ID3x" and r.headers["content-type"] == "audio/mpeg"
    r = client.get("/stream/audio/abcdef12", headers={"Range": "bytes=0-1"})
    assert r.status_code == 206 and r.headers["content-range"] == "bytes 0-1/4"
    assert client.get("/stream/audio/..%2Fetc").status_code == 404


def test_news_feed_parsing(client):
    items = sources.parse_feed(RSS.encode()) + sources.parse_feed(ATOM.encode())
    assert [i["title"] for i in items] == ["Bridge reopens", "Old story", "Comet seen"]
    assert "bridge" in items[0]["summary"] and items[0]["author"] == "Ana"
    assert items[0]["image"] == "https://news.example/b.jpg" and items[2]["source"] == "Atom Daily"
    titles = {client.get("/api/card/news").json()["title"] for _ in range(20)}
    assert titles == {"Bridge reopens", "Comet seen"}  # the 2001 item is too old


def test_trip_parsing():
    trips = context.parse_trips(fake_backend(httpx.Request("GET", "http://travel.test/api/trips",
                                                           headers={"authorization": "Bearer tt"})).json())
    japan = trips[0]
    assert japan.name == "Japan" and japan.start == date(2099, 4, 1)
    assert japan.places == ["Kyoto", "Osaka"] and japan.coords == [(35.01, 135.77)]
    assert context.parse_trips({"trips": [{"slug": "x"}]})[0].name == "x"
    assert context.parse_trips("garbage") == []


def test_event_validation(client):
    assert client.post("/api/event", json={"kind": "video", "action": "delete"}).status_code == 422
    assert client.post("/api/event", json={"kind": "nope", "action": "open"}).status_code == 400


# ------------------------------------------------------------------ curator

def fake_llm(calls):
    """Scores the first candidate 9, the second 2, and invents an id that was never offered.
    For photos, picks the first nearby article. For phrase cards, answers every id."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1/chat/completions":
            return fake_backend(request)
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["authorization"] == "Bearer llmk"
        content = body["messages"][1]["content"]
        text = content if isinstance(content, str) else "\n".join(p.get("text", "") for p in content)
        ids = re.findall(r"^\[([^\]]+)\]", text, re.M)
        if body["messages"][0]["content"] == curator.PHRASE_SYSTEM:
            out = {"cards": [{"id": i, "native": "你好", "romanization": "nǐ hǎo", "meaning": "hello",
                              "note": "Everyday greeting."} for i in ids] + [{"id": "phrase:Evil:x", "native": "x"}]}
        else:
            first = {"id": ids[0], "score": 9, "why": "Worth it."}
            m = re.search(r"^\s+- ([^:]+):", text, re.M)
            if m:
                first["article"] = m.group(1)
            out = {"picks": [first, {"id": "invented-id", "score": 10, "why": "x"}]}
            if len(ids) > 1:
                out["picks"].append({"id": ids[1], "score": 2, "why": "meh"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Sure:\n" + json.dumps(out)}}]})
    return handler


def curated_client(calls, **kw):
    s = full_settings(llm_url="http://llm.test", llm_key="llmk", invidious_topics=["bridges"], curator_share=1.0, **kw)
    store = Store(":memory:")
    app = create_app(s, transport=httpx.MockTransport(fake_llm(calls)), store=store, run_curator=False)
    return TestClient(app), store


def prompt_for(calls, needle):
    for x in calls:
        c = x["messages"][1]["content"]
        text = c if isinstance(c, str) else "\n".join(p.get("text", "") for p in c)
        if needle in text:
            return x, text
    raise AssertionError(f"no prompt containing {needle!r}")


def test_curator_end_to_end():
    calls = []
    c, store = curated_client(calls)
    with c:
        assert c.post("/api/event", json={"kind": "video", "ref": "openedvid01", "action": "open",
                                          "title": "Bridges 101"}).status_code == 204
        stats = c.post("/api/curate").json()
        assert set(stats) == {"video", "language", "wikipedia", "song", "news", "game", "photo"}, stats
        for kind in ("video", "language", "wikipedia", "song", "news", "game", "photo"):
            assert stats[kind]["picked"] == 1, (kind, stats)
        assert stats["language"]["phrases"] == 6

        _, prompt = prompt_for(calls, "These are videos")
        assert "Bridges 101" in prompt and "short1aaaaa" not in prompt and "[Nebula] Why ships float" in prompt
        assert prompt.index("[relatedvid1]") < prompt.index("[long1aaaaaa]")  # related first
        assert all(x["model"] == "fast-haiku" for x in calls)

        for kind in ("video", "wikipedia", "song", "news", "game", "photo"):
            card = c.get(f"/api/card/{kind}").json()
            assert card["why"] == "Worth it.", (kind, card)
            assert card["kind"] == kind and card["url"].startswith("https://")

        assert c.get("/api/sources").json()["curator"]["pools"]["video"] == 1
        assert c.post("/api/curate?kind=video").json()["video"] == {"candidates": 0, "picked": 0}
        assert c.post("/api/curate?kind=movie").status_code == 400


def test_wikipedia_prefers_personal_context():
    calls = []
    c, _ = curated_client(calls)
    with c:
        c.post("/api/curate?kind=wikipedia")
        _, prompt = prompt_for(calls, "These are Wikipedia articles")
        assert "(near home (Seattle)) Place at 47.6062" in prompt
        assert "(near your photos from 2026-10-02 (Leavenworth)) Place at 47.59" in prompt
        assert "Place at 47.61" not in prompt  # a photo taken at home is not a place to explore
        assert "(upcoming trip: Japan starting 2099-04-01) Place at 35.01" in prompt
        assert "(upcoming trip: Japan starting 2099-04-01) Search Kyoto" in prompt
        assert "Old trip" not in prompt
        assert "(interest: linguistics) Search linguistics" in prompt
        assert "[Wikipedia · Spanish] es mostviewed" in prompt
        assert "rewards three minutes" in prompt


def test_photo_gets_nearby_article_and_caption():
    calls = []
    c, store = curated_client(calls)
    with c:
        assert c.post("/api/curate?kind=photo").json()["photo"]["picked"] == 1
        body, prompt = prompt_for(calls, "Wikipedia articles near where it was taken")
        images = [p for p in body["messages"][1]["content"] if p["type"] == "image_url"]
        assert len(images) == 2 and images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        card = c.get("/api/card/photo").json()
        assert card["why"] == "Worth it." and card["label"] == "Photo · nearby on Wikipedia"
        # p0 was taken at latitude 35.0 and p1 at 36.0: the article must be one near that photo.
        lat = {"p0": "35.0", "p1": "36.0"}[card["ref"]]
        assert card["actions"][0] == {"label": f"Read: Place at {lat}",
                                      "url": f"https://en.m.wikipedia.org/wiki/Place_at_{lat}"}


def test_photo_without_vision_sends_text_only():
    calls = []
    c, _ = curated_client(calls, curator_vision=False)
    with c:
        c.post("/api/curate?kind=photo")
        body, _ = prompt_for(calls, "Wikipedia articles near where it was taken")
        assert isinstance(body["messages"][1]["content"], str)


def test_phrase_cards():
    calls = []
    c, store = curated_client(calls)
    with c:
        n = c.post("/api/curate?kind=language").json()["language"]["phrases"]
        assert n == 6
        body, prompt = prompt_for(calls, "Write one card per line")
        langs = {"Telugu", "Mandarin", "French", "Korean", "Cantonese", "Japanese", "Russian", "Ukrainian",
                 "Hindi", "Indonesian", "Tagalog"}
        assert all(re.search(r"\] (\w+):", line).group(1) in langs for line in prompt.splitlines() if line.startswith("[phrase:"))
        cards = [c.get("/api/card/language").json() for _ in range(12)]
        phrase = next(x for x in cards if x["ref"].startswith("phrase:"))
        assert phrase["title"] == "你好" and phrase["subtitle"] == "nǐ hǎo"
        assert phrase["url"] == "https://en.wiktionary.org/w/index.php?search=%E4%BD%A0%E5%A5%BD"
        assert "Written by the LLM" in phrase["blurb"]
        assert "phrase:Evil:x" not in store.known("language")


def test_parse_picks_rejects_junk():
    assert curator.parse_picks("no json here", {"a"}) == []
    assert curator.parse_picks("[1, 2]", {"a"}) == []
    got = curator.parse_picks('{"picks":[{"id":"a","score":"11","why":"x","article":"T"},{"id":"b","score":5}]}', {"a"})
    assert got == [{"id": "a", "score": 10, "why": "x", "article": "T"}]


# ------------------------------------------------------------ graph profile

PROFILE = {
    "person": {"id": "x", "kind": "person", "name": "Axl Liess", "visibility": "household"},
    "home": {"place": "Home", "lat": 47.65, "lon": -122.35, "address": "Fremont, Seattle"},
    "interests": {
        "love": [{"topic": "Pacific Northwest geology", "category": "subject"},
                 {"topic": "Backpacking", "category": "activity"}],
        "like": [{"topic": "Documentaries", "category": "genre"}],
        "curious": [{"topic": "Tagalog", "category": "language", "wikidata": "Q34057"},
                    {"topic": "Cantonese", "category": "language", "wikidata": "Q9186"}],
        "avoid": [{"topic": "Outrage politics", "category": "genre"}],
    },
    "languages": [
        {"language": "Mandarin Chinese", "level": "learning", "priority": 1, "wikidata": "Q9192"},
        {"language": "Telugu", "level": "learning", "priority": 2, "wikidata": "Q8097"},
        {"language": "English", "level": "native", "priority": None, "wikidata": "Q1860"},
        {"language": "Spanish", "level": "fluent", "priority": None, "wikidata": "Q1321"},
    ],
    "projects": [{"project": "WTR MAX migration", "status": "active", "role": None, "topics": ["Proxmox"]},
                 {"project": "Old blog", "status": "done", "role": None, "topics": ["Blogging"]}],
}


def graph_backend(posted, inner=fake_backend, profile_status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.test":
            assert request.headers["authorization"] == "Bearer gt"
            if request.url.path == "/api/profile/Axl":
                return httpx.Response(profile_status, json=PROFILE)
            if request.url.path == "/api/facts":
                body = json.loads(request.content)
                posted.append(body)
                return httpx.Response(201, json={"created": True, "status": "proposed", **body})
        if request.url.host == "en.wikipedia.org" and request.url.params.get("prop") == "pageprops":
            return httpx.Response(200, json={"query": {"pages": [
                {"title": request.url.params["titles"], "pageprops": {"wikibase_item": "Q123"}}]}})
        return inner(request)
    return handler


def test_profile_from_graph_replaces_env_defaults():
    from app import profile as prof
    s = prof.apply(full_settings(), PROFILE)
    assert s.languages_learning == [("Mandarin Chinese", "zh"), ("Telugu", "te")]
    assert s.languages_fluent == [("English", "en"), ("Spanish", "es")]
    assert s.languages_curious == [("Tagalog", "tl"), ("Cantonese", "zh-yue")]
    assert s.home_coords == (47.65, -122.35) and s.home_name == "Fremont, Seattle"
    assert s.wiki_interests == ["Pacific Northwest geology", "Backpacking", "Proxmox"]  # genres and done projects left out
    text = s.curator_profile
    assert "Loves: Pacific Northwest geology, Backpacking." in text
    assert "Learning Mandarin Chinese now, then Telugu." in text
    assert "Leave out anything that is mainly: Outrage politics." in text
    assert "Working on: WTR MAX migration." in text and "Old blog" not in text


def test_curator_prompts_use_the_graph_profile():
    calls, posted = [], []
    s = full_settings(llm_url="http://llm.test", llm_key="llmk", graph=Service("http://graph.test"),
                      graph_token="gt", curator_share=1.0)
    app = create_app(s, transport=httpx.MockTransport(graph_backend(posted, fake_llm(calls))),
                     store=Store(":memory:"), run_curator=False)
    with TestClient(app) as c:
        assert c.get("/api/sources").json()["profile"] == "graph"
        c.post("/api/curate?kind=wikipedia")
        _, prompt = prompt_for(calls, "These are Wikipedia articles")
        assert "Leave out anything that is mainly: Outrage politics" in prompt
        assert "(near home (Fremont, Seattle)) Place at 47.65" in prompt
        assert "(interest: " in prompt and "linguistics" not in prompt  # graph interests, not the test's env list
        labels = {c.get("/api/card/language").json()["label"] for _ in range(40)}
        assert any(l.startswith("Mandarin Chinese") for l in labels)


def test_graph_down_falls_back_to_env():
    posted = []
    s = full_settings(graph=Service("http://graph.test"), graph_token="gt")
    app = create_app(s, transport=httpx.MockTransport(graph_backend(posted, profile_status=503)),
                     store=Store(":memory:"), run_curator=False)
    with TestClient(app) as c:
        assert c.get("/api/sources").json()["profile"] == "env"
        assert c.get("/api/card/language").status_code == 200


def test_noticed_interests_are_proposed_with_evidence_once_a_week():
    import asyncio

    from app import curator as cur
    from app import profile as prof

    posted = []
    s = full_settings(llm_url="http://llm.test", llm_key="llmk", graph=Service("http://graph.test"), graph_token="gt")
    store = Store(":memory:")
    titles = ["Mount St. Helens", "Glacier Peak", "Mount Baker", "Lahar", "Bread", "Sourdough starter",
              "Mount Adams", "Mount Hood"]
    for t in titles:
        store.record("wikipedia", t, "open", t)

    async def fake_ask(client, s, prompt, system=""):
        assert system == prof.PROPOSE_SYSTEM and "pacific northwest geology" in prompt  # known topics, skipped
        return json.dumps({"interests": [
            {"topic": "Cascade Volcanoes", "category": "subject", "level": "like",
             "statement": "Has been reading about Cascade volcanoes",
             "evidence": ["Mount St. Helens", "Glacier Peak", "Mount Baker"]},
            {"topic": "Bread", "level": "like", "evidence": ["Bread"]},           # one item is not a pattern
            {"topic": "Backpacking", "level": "love", "evidence": titles[:3]},     # already known
            {"topic": "Invented", "level": "like", "evidence": ["Not opened", "Also not"]},
        ]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(graph_backend(posted))) as client:
            cache = prof.ProfileCache(s)
            await cache.get(client)
            from datetime import date as d
            return await prof.propose_interests(client, s, store, cache, fake_ask, today=d(2026, 11, 16))

    assert asyncio.run(run()) == 1
    assert len(posted) == 1
    body = posted[0]
    assert body["about"] == "Axl" and body["kind"] == "interest" and body["source"] == "liessure"
    assert body["details"] == {"topic": "Cascade Volcanoes", "level": "like", "category": "subject", "wikidata": "Q123"}
    assert body["source_ref"] == "interest:Q123:2026-W47"
    assert body["quote"] == "Mount St. Helens; Glacier Peak; Mount Baker"
    assert cur is not None
