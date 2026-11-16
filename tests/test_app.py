import json
import random

import httpx
import pytest
from fastapi.testclient import TestClient

from app import sources
from app.config import Service, Settings, parse_weights
from app.main import create_app, pick_kinds

JF = "http://jf.test"
IM = "http://im.test"
RM = "http://romm.test"
INV = "http://inv.test"


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
                "AlbumArtist": "Band", "AlbumId": "alb"}
        return httpx.Response(200, json={"Items": [item]})
    if host == "jf.test" and "/Images/" in path:
        return httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})
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
    return httpx.Response(404, text=f"unexpected {url}")


def full_settings() -> Settings:
    return Settings(
        jellyfin=Service(JF, "https://jellyfin.example"), jellyfin_token="jft", jellyfin_user_id="u1",
        immich=Service(IM, "https://photos.example"), immich_api_key="imk",
        romm=Service(RM, "https://games.example"), romm_token="rt",
        invidious=Service(INV, "https://watch.example"), invidious_topics=["docs"],
        language_practice_url="https://practice.example", language_video_queries=["learn telugu"],
    )


@pytest.fixture
def client():
    app = create_app(full_settings(), transport=httpx.MockTransport(fake_backend))
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
    app = create_app(Settings(), transport=httpx.MockTransport(fake_backend))
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
    assert r.status_code == 200 and "Detour" in r.text
    assert client.get("/app.js").status_code == 200
