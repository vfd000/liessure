import json
import random
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app import sources
from app.config import Service, Settings, parse_weights
from app import curator
from app.main import create_app, pick_kinds
from app.store import Store

JF = "http://jf.test"
IM = "http://im.test"
RM = "http://romm.test"
INV = "http://inv.test"


RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<title>Test Wire</title>
<item><title>Bridge reopens</title><link>https://news.example/bridge</link>
<description>&lt;p&gt;The &lt;b&gt;bridge&lt;/b&gt; reopened.&lt;/p&gt;</description>
<pubDate>Mon, 16 Nov 2099 10:00:00 GMT</pubDate><media:content url="https://news.example/b.jpg"/></item>
<item><title>Old story</title><link>https://news.example/old</link><pubDate>Mon, 01 Jan 2001 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Daily</title>
<entry><title>Comet seen</title><link rel="alternate" href="https://atom.example/comet"/>
<summary>Bright one.</summary><updated>2099-11-16T09:00:00Z</updated></entry></feed>"""


async def _agen(data):
    yield data


def fake_backend(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    host, path = request.url.host, request.url.path
    if host.endswith("wikipedia.org"):
        return httpx.Response(200, json={
            "type": "standard", "title": "Lighthouse", "description": "tower",
            "extract": "A lighthouse is a tower. " * 20,
            "thumbnail": {"source": "https://upload.wikimedia.org/x.jpg"},
            "content_urls": {"mobile": {"page": "https://en.m.wikipedia.org/wiki/Lighthouse"}},
        })
    if host == "jf.test" and path == "/Items":
        assert "Token=\"jft\"" in request.headers["authorization"]
        t = request.url.params["IncludeItemTypes"]
        item = {"Id": "abc", "ServerId": "srv", "Name": f"A {t}", "ProductionYear": 1999,
                "Genres": ["Drama"], "ImageTags": {"Primary": "t"}, "Album": "LP",
                "AlbumArtist": "Band", "AlbumId": "alb",
                "RemoteTrailers": [{"Url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}]}
        return httpx.Response(200, json={"Items": [item]})
    if host == "jf.test" and "/Images/" in path:
        return httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})
    if host == "jf.test" and path.startswith("/Audio/"):
        assert "Token=\"jft\"" in request.headers["authorization"]
        if request.headers.get("range") == "bytes=0-1":
            return httpx.Response(206, content=_agen(b"ID"), headers={"content-type": "audio/mpeg", "content-range": "bytes 0-1/4"})
        return httpx.Response(200, content=_agen(b"ID3x"), headers={"content-type": "audio/mpeg"})
    if host == "im.test" and path == "/api/search/random":
        assert request.headers["x-api-key"] == "imk"
        return httpx.Response(200, json=[{"id": "p1", "localDateTime": "2024-05-01T10:00:00",
                                          "exifInfo": {"city": "Seattle", "country": "USA"}}])
    if host == "im.test" and path == "/api/memories":
        return httpx.Response(200, json=[])
    if host == "romm.test" and path == "/api/roms":
        assert request.headers["authorization"].startswith("Bearer ")
        return httpx.Response(200, json={"items": [{"id": 7, "name": "Zelda", "platform_display_name": "SNES",
                                                    "path_cover_large": "roms/1/7/cover/big.png"}], "total": 40})
    if host == "inv.test" and path == "/api/v1/search":
        return httpx.Response(200, json=[
            {"type": "video", "videoId": "short1", "title": "short", "lengthSeconds": 30},
            {"type": "video", "videoId": "long1", "title": "A long one", "author": "Ch", "lengthSeconds": 900},
        ])
    if host == "news.test" and path == "/rss":
        return httpx.Response(200, content=RSS.encode(), headers={"content-type": "application/rss+xml"})
    if host == "news.test" and path == "/atom":
        return httpx.Response(200, content=ATOM.encode(), headers={"content-type": "application/atom+xml"})
    return httpx.Response(404, text=f"unexpected {url}")


def full_settings() -> Settings:
    return Settings(
        jellyfin=Service(JF, "https://jellyfin.example"), jellyfin_token="jft", jellyfin_user_id="u1",
        immich=Service(IM, "https://photos.example"), immich_api_key="imk",
        romm=Service(RM, "https://games.example"), romm_token="rt",
        invidious=Service(INV, "https://watch.example"), invidious_topics=["docs"],
        language_practice_url="https://practice.example", language_video_queries=["learn telugu"],
        news_feeds=["http://news.test/rss", "http://news.test/atom"],
    )


@pytest.fixture
def client():
    app = create_app(full_settings(), transport=httpx.MockTransport(fake_backend), store=Store(":memory:"), run_curator=False)
    with TestClient(app) as c:
        yield c


def test_every_kind_produces_a_card(client):
    for kind in sources.SOURCES:
        r = client.get(f"/api/card/{kind}")
        assert r.status_code == 200, (kind, r.text)
        card = r.json()
        assert card["kind"] == kind and card["title"] and card["url"].startswith("https://")


def test_links_use_public_urls(client):
    assert client.get("/api/card/movie").json()["url"] == "https://jellyfin.example/web/#/details?id=abc&serverId=srv"
    assert client.get("/api/card/photo").json()["url"] == "https://photos.example/photos/p1"
    g = client.get("/api/card/game").json()
    assert g["url"] == "https://games.example/rom/7"
    assert g["image"].startswith("/img?src=http%3A%2F%2Fromm.test%2Fassets%2Fromm%2Fresources%2Froms")
    assert client.get("/api/card/video").json()["url"] == "https://watch.example/watch?v=long1"


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
    app = create_app(Settings(), transport=httpx.MockTransport(fake_backend), store=Store(":memory:"), run_curator=False)
    with TestClient(app) as c:
        s = c.get("/api/sources").json()["sources"]
        assert s["wikipedia"]["configured"] and not s["movie"]["configured"]
        cards = c.get("/api/feed?n=6").json()["cards"]
        assert cards and {x["kind"] for x in cards} <= {"wikipedia", "language"}


def test_weights_and_pick():
    w = parse_weights("photo=0,game=9,bogus=3,video=x")
    assert w["photo"] == 0 and w["game"] == 9 and w["video"] == 3
    picked = pick_kinds(w, {k: True for k in w}, 50, random.Random(1))
    assert "photo" not in picked


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Liessure" in r.text
    assert client.get("/app.js").status_code == 200


def test_inline_embeds(client):
    song = client.get("/api/card/song").json()
    assert song["embed"]["type"] == "audio" and song["embed"]["src"] == "/stream/audio/abc"
    movie = client.get("/api/card/movie").json()
    assert movie["embed"]["src"] == "https://watch.example/embed/dQw4w9WgXcQ?autoplay=1"
    assert client.get("/api/card/video").json()["embed"]["src"].startswith("https://watch.example/embed/long1")


def test_audio_stream_proxy(client):
    r = client.get("/stream/audio/abcdef12")
    assert r.status_code == 200 and r.content == b"ID3x" and r.headers["content-type"] == "audio/mpeg"
    r = client.get("/stream/audio/abcdef12", headers={"Range": "bytes=0-1"})
    assert r.status_code == 206 and r.headers["content-range"] == "bytes 0-1/4"
    assert client.get("/stream/audio/..%2Fetc").status_code == 404




def test_news_feed_parsing(client):
    from app import sources as src
    items = src.parse_feed(RSS.encode()) + src.parse_feed(ATOM.encode())
    assert [i["title"] for i in items] == ["Bridge reopens", "Old story", "Comet seen"]
    assert items[0]["summary"] == "The  bridge  reopened." or "bridge" in items[0]["summary"]
    assert items[0]["image"] == "https://news.example/b.jpg" and items[2]["source"] == "Atom Daily"
    titles = {client.get("/api/card/news").json()["title"] for _ in range(20)}
    assert titles == {"Bridge reopens", "Comet seen"}  # the 2001 item is too old


# ------------------------------------------------------------------ curator

def curator_backend(calls):
    """Fake services plus a fake gateway that scores the first candidate 9, the second 2,
    and invents an id that was never offered."""
    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "inv.test" and path == "/api/v1/search":
            return httpx.Response(200, json=[
                {"type": "video", "videoId": "goodvideo01", "title": "How bridges stand up", "author": "Eng", "lengthSeconds": 900},
                {"type": "video", "videoId": "dramavideo1", "title": "YOU WON'T BELIEVE", "author": "X", "lengthSeconds": 600},
                {"type": "video", "videoId": "shortvideo1", "title": "short", "author": "X", "lengthSeconds": 40},
            ])
        if host == "inv.test" and path.startswith("/api/v1/videos/"):
            return httpx.Response(200, json={"recommendedVideos": [
                {"videoId": "relatedvid1", "title": "Suspension bridges", "author": "Eng", "lengthSeconds": 1200}]})
        if host.endswith("wikipedia.org") and path == "/w/api.php":
            gen = request.url.params["generator"]
            pages = [{"ns": 0, "title": f"{gen} article {n}", "description": "d", "extract": "Long text. " * 40}
                     for n in range(2)]
            return httpx.Response(200, json={"query": {"pages": pages}})
        if host == "jf.test" and (path == "/Items" or path.endswith("/Similar")):
            return httpx.Response(200, json={"Items": [
                {"Id": f"aaaa000{n}", "Name": f"Track {n}", "AlbumArtist": "Band", "Album": "LP"} for n in range(2)]})
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            calls.append(body)
            assert request.headers["authorization"] == "Bearer llmk"
            ids = re.findall(r"^\[([^\]]+)\]", body["messages"][1]["content"], re.M)
            picks = [{"id": ids[0], "score": 9, "why": "Worth it."}, {"id": "invented-id", "score": 10, "why": "x"}]
            if len(ids) > 1:
                picks.append({"id": ids[1], "score": 2, "why": "meh"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "Sure:\n" + json.dumps({"picks": picks})}}]})
        return fake_backend(request)
    return handler


def test_curator_end_to_end():
    calls = []
    s = full_settings()
    s.llm_url, s.llm_key, s.invidious_topics = "http://llm.test", "llmk", ["bridges"]
    s.curator_share = 1.0
    store = Store(":memory:")
    app = create_app(s, transport=httpx.MockTransport(curator_backend(calls)), store=store, run_curator=False)
    with TestClient(app) as c:
        assert c.post("/api/event", json={"kind": "video", "ref": "openedvid01", "action": "open",
                                          "title": "Bridges 101"}).status_code == 204
        stats = c.post("/api/curate").json()
        assert set(stats) == {"video", "language", "wikipedia", "song", "news"}, stats
        assert all(v["picked"] == 1 for v in stats.values()), stats

        video_prompt = next(x for x in calls if "These are videos" in x["messages"][1]["content"])
        prompt = video_prompt["messages"][1]["content"]
        assert "Bridges 101" in prompt and "shortvideo1" not in prompt
        assert prompt.index("[relatedvid1]") < prompt.index("[goodvideo01]")  # related first
        assert all(x["model"] == "fast-haiku" for x in calls)

        for kind in stats:
            card = c.get(f"/api/card/{kind}").json()
            assert card["why"] == "Worth it.", (kind, card)
            assert card["kind"] == kind and card["url"].startswith("https://")

        assert c.get("/api/sources").json()["curator"]["pools"]["video"] == 1
        # Second run: everything already scored, nothing new to send for the stable sources.
        again = c.post("/api/curate?kind=video").json()
        assert again == {"video": {"candidates": 0, "picked": 0}}
        assert c.post("/api/curate?kind=movie").status_code == 400


def test_parse_picks_rejects_junk():
    assert curator.parse_picks("no json here", {"a"}) == []
    assert curator.parse_picks("[1, 2]", {"a"}) == []
    got = curator.parse_picks('{"picks":[{"id":"a","score":"11","why":"x"},{"id":"b","score":5}]}', {"a"})
    assert got == [{"id": "a", "score": 10, "why": "x"}]


def test_event_validation(client):
    assert client.post("/api/event", json={"kind": "video", "action": "delete"}).status_code == 422
    assert client.post("/api/event", json={"kind": "nope", "action": "open"}).status_code == 400
