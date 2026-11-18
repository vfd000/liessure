import asyncio
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


PHRACK_HTML = """<html><head><link rel="canonical" href="https://phrack.org/issues/73/introduction_md.html"></head><body>
<a href="/issues/73/introduction_md.html#article">Introduction</a>
<a href="/issues/72/old-thing.html#article">An old one</a>
<a href="/issues/73/escaping-from-a-docker-container-through-vdso_md.html#article">Escaping from a Docker container through vDSO</a>
<a href="/issues/73/beyond-the-facade-exposing-smart-giants.html#article">Beyond the Facade :: Exposing Smart Giants</a>
</body></html>"""


PHRACK_TEXT = """<html><body><pre>
|=-----------------------------------------------------------------------=|
|=----------------=[ Word Machines for Weird Machines ]=-----------------=|
|=-----------------------------------------------------------------------=|
|=--------------------------=[ noperator ]=------------------------------=|
|=-----------------------------------------------------------------------=|

--[ 0 - Intro

Phrack is said to be a snapshot of hacker culture at each issue, so I
should open by illustrating my own first-person PoV at the time of this
writing &amp; more.

    int main(void) {
        return 0;
    }

--[ 1 - Results
</pre></body></html>"""


KQUIZ_JSON = '''{"results": {"bindings": [{"p": {"value": "http://www.wikidata.org/entity/Q1"}, "name": {"value": "Actor 1"}, "ko": {"value": "배우1"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a1.jpg"}, "dob": {"value": "1971-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581097"}}, {"p": {"value": "http://www.wikidata.org/entity/Q2"}, "name": {"value": "Actor 2"}, "ko": {"value": "배우2"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a2.jpg"}, "dob": {"value": "1972-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581072"}}, {"p": {"value": "http://www.wikidata.org/entity/Q3"}, "name": {"value": "Actor 3"}, "ko": {"value": "배우3"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a3.jpg"}, "dob": {"value": "1973-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581097"}}, {"p": {"value": "http://www.wikidata.org/entity/Q4"}, "name": {"value": "Actor 4"}, "ko": {"value": "배우4"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a4.jpg"}, "dob": {"value": "1974-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581072"}}, {"p": {"value": "http://www.wikidata.org/entity/Q5"}, "name": {"value": "Actor 5"}, "ko": {"value": "배우5"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a5.jpg"}, "dob": {"value": "1975-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581097"}}, {"p": {"value": "http://www.wikidata.org/entity/Q6"}, "name": {"value": "Actor 6"}, "ko": {"value": "배우6"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a6.jpg"}, "dob": {"value": "1976-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581072"}}, {"p": {"value": "http://www.wikidata.org/entity/Q7"}, "name": {"value": "Actor 7"}, "ko": {"value": "배우7"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a7.jpg"}, "dob": {"value": "1977-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581097"}}, {"p": {"value": "http://www.wikidata.org/entity/Q8"}, "name": {"value": "Actor 8"}, "ko": {"value": "배우8"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a8.jpg"}, "dob": {"value": "1978-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581072"}}, {"p": {"value": "http://www.wikidata.org/entity/Q9"}, "name": {"value": "Actor 9"}, "ko": {"value": "배우9"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a9.jpg"}, "dob": {"value": "1979-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581097"}}, {"p": {"value": "http://www.wikidata.org/entity/Q10"}, "name": {"value": "Actor 10"}, "ko": {"value": "배우10"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a10.jpg"}, "dob": {"value": "1980-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581072"}}, {"p": {"value": "http://www.wikidata.org/entity/Q11"}, "name": {"value": "Actor 11"}, "ko": {"value": "배우11"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a11.jpg"}, "dob": {"value": "1981-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581097"}}, {"p": {"value": "http://www.wikidata.org/entity/Q12"}, "name": {"value": "Actor 12"}, "ko": {"value": "배우12"}, "img": {"value": "http://commons.wikimedia.org/wiki/Special:FilePath/a12.jpg"}, "dob": {"value": "1982-06-15T00:00:00Z"}, "gender": {"value": "http://www.wikidata.org/entity/Q6581072"}}]}}'''


def fake_backend(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    host, path, q = request.url.host, request.url.path, request.url.params
    if host == "simple.wikipedia.org" and path == "/api/rest_v1/page/random/summary":
        return httpx.Response(200, json={"type": "standard", "title": "Volcano",
            "extract": "Volcanoes are openings in the ground. Hot rock comes out of them and builds mountains. " * 4,
            "content_urls": {"mobile": {"page": "https://simple.m.wikipedia.org/wiki/Volcano"}}})
    if host.endswith("wikipedia.org") and path.startswith("/api/rest_v1/page/random"):
        return httpx.Response(200, json={
            "type": "standard", "title": "Lighthouse", "description": "tower",
            "coordinates": {"lat": 47.73, "lon": -122.03},
            "extract": "A lighthouse is a tower. " * 20,
            "thumbnail": {"source": "https://upload.wikimedia.org/x.jpg"},
            "content_urls": {"mobile": {"page": f"https://{host.split('.')[0]}.m.wikipedia.org/wiki/Lighthouse"}},
        })
    if host.endswith("wikipedia.org") and path.startswith("/api/rest_v1/page/summary/"):
        title = path.rsplit("/", 1)[1].replace("_", " ")
        return httpx.Response(200, json={
            "type": "standard", "title": title, "description": "software", "extract": f"{title} is software. " * 10,
            "content_urls": {"mobile": {"page": f"https://{host.split('.')[0]}.m.wikipedia.org/wiki/{title}"}},
        })
    if host == "grafana.test":
        assert request.headers["authorization"] == "Bearer gt"
        if path == "/api/prometheus/grafana/api/v1/alerts":
            alerts = [] if "noalerts" in request.headers.get("x-test", "") else [
                {"labels": {"alertname": "Disk almost full", "instance": "wtr"}, "state": "Alerting",
                 "annotations": {"summary": "/mnt/6tb is 93% full"}, "activeAt": "2026-11-17T01:02:03Z"},
                {"labels": {"alertname": "Normal one"}, "state": "Normal"}]
            return httpx.Response(200, json={"data": {"alerts": alerts}})
        if path.endswith("/api/v1/query"):
            if "node_filesystem" in q["query"]:
                return httpx.Response(200, json={"data": {"result": [
                    {"metric": {"mountpoint": "/mnt/6tb", "instance": "wtr"}, "value": [0, "0.8213"]}]}})
            return httpx.Response(200, json={"data": {"result": []}})
    if host == "ha.test":
        assert request.headers["authorization"] == "Bearer ht"
        if path == "/api/states":
            return httpx.Response(200, json=[
                {"entity_id": "sensor.envoy_1_lifetime_energy_production", "state": "12.30",
                 "attributes": {"unit_of_measurement": "MWh"}},
                {"entity_id": "sensor.envoy_1_lifetime_energy_consumption", "state": "20.004",
                 "attributes": {"unit_of_measurement": "MWh"}},
                {"entity_id": "sensor.envoy_1_current_power_production", "state": "2100",
                 "attributes": {"unit_of_measurement": "W"}},
                {"entity_id": "weather.forecast_home", "state": "partlycloudy",
                 "attributes": {"temperature": 48, "temperature_unit": "°F", "humidity": 80}},
            ])
        if path.startswith("/api/history/period/"):
            eid = q["filter_entity_id"]
            # Lifetime counters (MWh): day before yesterday's close, last midnight, earlier today.
            series = {"sensor.envoy_1_lifetime_energy_production": ["12.270", "12.288", "12.290"],
                      "sensor.envoy_1_lifetime_energy_consumption": ["19.980", "19.999", "20.000"]}[eid]
            times = ["2026-11-15T23:59:00-08:00", "2026-11-16T23:59:00-08:00", "2026-11-17T08:00:00-08:00"]
            return httpx.Response(200, json=[[{"state": v, "last_changed": t} for v, t in zip(series, times)]])
    if host == "phrack.org" and path == "/issues/73/word-machines-for-weird-machines.html":
        return httpx.Response(200, text=PHRACK_TEXT)
    if host == "query.wikidata.org" and path == "/sparql":
        return httpx.Response(200, text=KQUIZ_JSON)
    if host == "phrack.org" and path == "/index_latest.html":
        return httpx.Response(200, text=PHRACK_HTML)
    if host == "jobs.test" and path.startswith("/api/"):
        assert request.headers["authorization"] == "Bearer jt"
        job = lambda n, **kw: {"id": n, "title": f"Security Engineer {n}", "company": f"Co{n}", "location": "Seattle",
                               "workplace": "hybrid", "salary": "$200k", "score": 80 + n, "status": "new",
                               "summary": "Good fit.", "url": f"https://jobs.example/#/job/{n}",
                               "posting_url": f"https://boards.example/{n}", **kw}
        if path == "/api/jobs":
            return httpx.Response(200, json=[job(1), job(2), job(3, status="applied")])
        if path.startswith("/api/jobs/"):
            n = int(path.rsplit("/", 1)[1])
            gaps = ["Kubernetes in production", "Go"] if n != 2 else ["kubernetes in production!"]
            return httpx.Response(200, json={**job(n), "score_detail": {"gaps": gaps},
                                             "jd_text": f"About the role {n}.\nYou will secure things."})
        if path == "/api/pipeline":
            return httpx.Response(200, json={"board": {"interviewing": [
                job(9, status="interviewing", next_step="Onsite loop", next_step_on="2026-11-18T09:00:00")]}})
    if host.endswith("wikipedia.org") and path == "/w/api.php" and q.get("list") == "search":
        return httpx.Response(200, json={"query": {"search": [{"title": "Geology of the Pacific Northwest"}]}})
    if host.endswith("wikipedia.org") and path == "/w/api.php" and q.get("prop") == "extracts":
        return httpx.Response(200, json={"query": {"pages": [{"extract": "The Cascade Volcanoes run north to south. " * 40}]}})
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
    if host == "jf.test" and path == "/Items" and q.get("ids"):
        codec = "alac" if q["ids"] == "a1ac0001" else "flac"
        return httpx.Response(200, json={"Items": [{"Id": q["ids"], "MediaSources": [
            {"MediaStreams": [{"Type": "Audio", "Codec": codec}]}]}]})
    if host == "jf.test" and (path == "/Shows/NextUp" or path.endswith("/Episodes")):
        ep = {"Id": "e0e0e0e1", "ServerId": "srv", "Name": "Cat and Mouse", "SeriesName": "The Clone Wars",
              "SeriesId": "abc12345", "ParentIndexNumber": 2, "IndexNumber": 16, "Overview": "A blockade.",
              "ImageTags": {"Primary": "t"}}
        if path == "/Shows/NextUp" and not q.get("seriesId"):
            ep = {**ep, "Id": "e0e0e0e2", "Name": "Hunted", "IndexNumber": 17}
        return httpx.Response(200, json={"Items": [ep]})
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
    if host == "inv.test" and path.startswith("/api/") and request.headers.get("x-watch-token") not in (None, "wt"):
        return httpx.Response(403)
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
        homelab_feeds=["http://news.test/rss"], homelab_topics=["homelab tour"],
        infosec_feeds=["http://news.test/atom"],
        jobs=Service("http://jobs.test", "https://jobs.example"), jobs_token="jt",
        grafana=Service("http://grafana.test", "https://telemetry.example"), grafana_token="gt",
        ha=Service("http://ha.test", "https://ha.example"), ha_token="ht",
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture(autouse=True)
def clear_caches():
    for c in (sources._news_cache, sources._nebula_cache, sources._inv_cache, sources._romm_total,
              sources._homelab_cache, sources._jobs_cache, sources._gaps_cache,
              sources._telemetry_cache, sources._home_cache, sources._stats_cache, sources._todo_cache,
              sources._infosec_cache, sources._phrack_cache):
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
        if kind in ("recall", "todo", "bite", "lesson", "career", "kquiz", "flashcard", "trivia", "session"):
            continue  # need the LLM: test_recall_questions_..., test_todo_card_endpoint
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
    assert {"label": "Play in Symfonium", "app": "app.symfonik.music.player",
            "song": {"title": "A Audio", "artist": "Band", "album": "LP"}} in song["actions"]
    assert song["open_in_app"] and client.get("/api/card/movie").json()["open_in_app"]
    assert not client.get("/api/card/news").json()["open_in_app"]


def test_feed(client):
    r = client.get("/api/feed?n=10").json()
    kinds = [c["kind"] for c in r["cards"]]
    assert len(kinds) >= 9  # a weekend drops the one job card
    assert all(a != b for a, b in zip(kinds, kinds[1:]))
    assert kinds.count("job") <= 1 and kinds.count("skill") <= 1


def test_job_and_skill_cards_are_capped_per_load():
    w = {k: 0 for k in parse_weights("")} | {"job": 9, "skill": 9, "wikipedia": 1}
    avail = {k: True for k in w}
    for seed in range(20):
        picked = pick_kinds(w, avail, 8, random.Random(seed))
        assert picked.count("job") <= 1 and picked.count("skill") <= 1 and len(picked) == 8
    assert pick_kinds({"job": 5}, {"job": True}, 6, random.Random(1)) == ["job"]


def test_homelab_cards():
    with make_client(full_settings()) as c:
        labels = {c.get("/api/card/homelab").json()["label"] for _ in range(30)}
    assert "Home lab · in your stack" in labels and "Home lab · video" in labels
    assert "Home lab · Test Wire" in labels


@pytest.mark.anyio
async def test_job_cards_respect_weekends_and_due_steps():
    s = full_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_backend)) as hc:
        # Monday, onsite on Wednesday (> 2 days): a new match, never the applied one.
        for seed in range(10):
            card = await sources.job(hc, s, random.Random(seed), today=date(2026, 11, 9))
            assert card.label == "Job · new match" and card.ref in ("1", "2")
            assert card.url == f"https://jobs.example/#/job/{card.ref}"
            assert {"label": "Posting", "url": f"https://boards.example/{card.ref}"} in card.actions
        sources._jobs_cache._data.clear()
        # Saturday with nothing due: no job card at all.
        with pytest.raises(sources.Unavailable):
            await sources.job(hc, s, random.Random(1), today=date(2026, 11, 7))
        sources._jobs_cache._data.clear()
        # Sunday the 15th... onsite the 18th is 3 days out: still nothing. Monday the 16th: the due step can show.
        with pytest.raises(sources.Unavailable):
            await sources.job(hc, s, random.Random(1), today=date(2026, 11, 15))
        sources._jobs_cache._data.clear()
        labels = {(await sources.job(hc, s, random.Random(n), today=date(2026, 11, 16))).label for n in range(12)}
        assert labels == {"Job · new match", "Job · next step due"}


def test_skill_cards_rank_gaps_across_jobs(client):
    card = client.get("/api/card/skill").json()
    assert card["kind"] == "skill" and card["url"] == "https://jobs.example/#/career"
    gaps = {client.get("/api/card/skill").json()["title"].casefold().rstrip("!") for _ in range(20)}
    assert gaps <= {"kubernetes in production", "go"}
    k8s = next(c for c in (client.get("/api/card/skill").json() for _ in range(30)) if c["title"].startswith("Kub"))
    assert k8s["subtitle"] == "3 of your top matches ask for this"
    assert k8s["embed"]["src"].startswith("https://watch.example/embed/")


def test_job_titles_never_reach_interest_proposals():
    store = Store(":memory:")
    for n in range(10):
        store.record("job", str(n), "open", f"Staff Engineer at Acme {n}")
    store.record("wikipedia", "Lighthouse", "open", "Lighthouse")
    seen = {}

    async def ask(client, s, prompt, system=None):
        seen["prompt"] = prompt
        return "{}"
    import asyncio
    from app import profile as profile_mod
    s = full_settings(graph=Service("http://graph.test"), graph_token="g")
    asyncio.run(profile_mod.propose_interests(None, s, store, profile_mod.ProfileCache(s), ask))
    assert "Acme" not in seen.get("prompt", "")


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
        assert cards and {x["kind"] for x in cards} <= {"wikipedia", "language", "homelab", "math", "infosec"}


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
        assert set(stats) == {"video", "language", "wikipedia", "song", "news", "game", "photo", "recall"}, stats
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


def test_phrase_links_speak_instead_of_forvo():
    acts = curator.phrase_actions("廁所喺邊度?", "zh-yue")
    assert acts[0] == {"label": "Hear it", "speak": {"text": "廁所喺邊度?", "lang": "zh-HK", "fallback": acts[1]["url"]}}
    assert acts[1]["label"] == "Google Translate" and "sl=yue" in acts[1]["url"] and "tl=en" in acts[1]["url"]
    assert acts[1]["in_app"] == "1"
    assert curator.phrase_actions("नमस्ते", "hi")[0]["speak"]["lang"] == "hi-IN"
    assert curator.phrase_url("廁所喺邊度?").endswith("%E5%BA%A6")  # no trailing "?"
    s = full_settings()
    assert curator.code_for_label(s, "Cantonese · where is the bathroom?") == "zh-yue"
    assert curator.code_for_label(s, "Klingon · hello") == ""


def test_old_forvo_phrase_cards_are_rewritten_when_served():
    st = Store(":memory:")
    old = sources.Card(kind="language", label="Cantonese · where is the bathroom?", title="廁所喺邊度?",
                       url="https://en.wiktionary.org/w/index.php?search=x",
                       actions=[{"label": "Hear it on Forvo", "url": "https://forvo.com/word/x/#yue"}],
                       ref="phrase:Cantonese:where").to_dict()
    st.add_picks("language", [{"ref": old["ref"], "card": old, "reason": "", "score": 7}], rejected=[])
    s = full_settings(llm_url="http://llm.test", llm_key="k", curator_share=1.0)
    app = create_app(s, transport=httpx.MockTransport(fake_backend), store=st, run_curator=False)
    with TestClient(app) as c:
        card = c.get("/api/card/language").json()
    assert card["actions"][0]["speak"]["lang"] == "zh-HK" and "forvo" not in json.dumps(card)


def test_ideas_are_kept_and_listed(client):
    r = client.post("/api/ideas", json={"text": "  Show trail conditions  ", "about": "photo: Riverbend"})
    assert r.status_code == 201
    idea_id = r.json()["id"]
    assert client.post("/api/ideas", json={"text": ""}).status_code == 422
    ideas = client.get("/api/ideas").json()["ideas"]
    assert ideas[0]["text"] == "Show trail conditions" and ideas[0]["about"] == "photo: Riverbend"
    assert ideas[0]["status"] == "new"
    assert client.post(f"/api/ideas/{idea_id}/status", json={"status": "planned"}).status_code == 200
    assert client.post(f"/api/ideas/{idea_id}/status", json={"status": "bogus"}).status_code == 422
    assert client.post("/api/ideas/999/status", json={"status": "done"}).status_code == 404
    assert client.get("/api/ideas?status=planned").json()["ideas"][0]["id"] == idea_id
    assert client.get("/api/ideas?status=new").json()["ideas"] == []


def test_telemetry_alerts_first_then_trends(client):
    card = client.get("/api/card/telemetry").json()
    assert card["title"] == "Disk almost full" and card["label"] == "Lab health · 1 firing"
    assert card["blurb"] == "/mnt/6tb is 93% full" and card["url"] == "https://telemetry.example/alerting/list"
    sources._telemetry_cache.put("alerts", [])
    card = client.get("/api/card/telemetry").json()
    assert card["title"] == "/mnt/6tb is 82% full" and card["label"] == "Lab health · Fullest disk"


@pytest.mark.anyio
async def test_home_solar_from_lifetime_counters():
    from datetime import datetime, timedelta, timezone
    s = full_settings()
    now = datetime(2026, 11, 17, 14, 0, tzinfo=timezone(timedelta(hours=-8)))
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_backend)) as hc:
        cards = {}
        for seed in range(12):
            c = await sources.home(hc, s, random.Random(seed), now=now)
            cards[c.label] = c
    solar = cards["Home · solar"]
    # 12.300 MWh now - 12.288 at midnight = 12 kWh today; 12.288 - 12.270 = 18 kWh yesterday.
    assert solar.title == "12.0 kWh of sun so far today"
    assert "Yesterday: 18.0 kWh." in solar.blurb and "used 5.0 kWh" in solar.blurb and "surplus of 7.0" in solar.blurb
    assert solar.subtitle == "Producing 2100 W right now"
    assert solar.url == "https://ha.example/energy" and solar.actions[0]["url"] == "homeassistant://navigate/energy"
    assert cards["Home · weather"].title == "48°F and partly cloudy outside"


def test_math_answers_are_exact_numbers():
    from fractions import Fraction
    from app import mathgen
    for gen, _ in mathgen.GENERATORS:
        for seed in range(60):
            p = gen(random.Random(seed))
            assert p.question and p.hint and p.solution, gen.__name__
            float(Fraction(p.answer))  # an integer, fraction or decimal the page can parse
    p = mathgen.last_digit(random.Random(3))
    a, n = (int(x) for x in re.search(r"of (\d)\^(\d+)", p.question).groups())
    assert p.answer == str(a ** n % 10)
    p = mathgen.system(random.Random(5))
    assert "x + y" in p.question


def test_math_card_offers_a_quiz(client):
    card = client.get("/api/card/math").json()
    assert card["kind"] == "math" and card["label"].startswith("Math · ")
    assert set(card["quiz"]) == {"answer", "tolerance", "hint", "solution", "scratch", "topic"}
    tutor = card["actions"][0]
    assert tutor["label"] == "Tutor me" and tutor["url"].startswith("https://claude.ai/new?q=")
    from urllib.parse import parse_qs, urlparse
    q = parse_qs(urlparse(tutor["url"]).query)["q"][0]
    import base64
    assert card["title"] in q and "Don't give me the answer" in q
    assert base64.b64encode(card["quiz"]["answer"].encode()).decode() in q  # there for Claude, not readable at a glance
    assert f"the answer is {card['quiz']['answer']}" not in q
    assert card["url"].startswith("https://en.wikipedia.org/wiki/")


def test_recall_questions_from_cards_you_opened():
    import asyncio
    import time as _time
    from app import store as store_mod
    st = Store(":memory:")
    s = full_settings(llm_url="http://llm.test", llm_key="k")
    with make_client(s) as c:
        app_store = c.app.state.store
        ev = {"kind": "wikipedia", "ref": "Lighthouse", "action": "open", "title": "Lighthouse",
              "subtitle": "tower", "text": "A lighthouse is a tower that emits light. The Pharos was built in 280 BC.",
              "url": "https://en.wikipedia.org/wiki/Lighthouse"}
        assert c.post("/api/event", json=ev).status_code == 204
        # Job text is never kept for questions.
        c.post("/api/event", json={**ev, "kind": "job", "ref": "7", "url": "https://jobs.example/#/job/7"})
        assert app_store.cards_to_ask(min_age_hours=0)[0]["ref"] == "Lighthouse"
        assert app_store.cards_to_ask(min_age_hours=1) == []  # too fresh to quiz yet
        assert len(app_store.cards_to_ask(min_age_hours=0)) == 1
        seen_prompt = {}

        async def ask(client, s, prompt, system=None):
            seen_prompt["p"], seen_prompt["sys"] = prompt, system
            return json.dumps({"questions": [
                {"id": 0, "question": "When was the Pharos built?", "choices": ["280 BC", "1200", "1850", "50 BC"],
                 "answer": 0, "explain": "The card says 280 BC."},
                {"id": 0, "question": "bad", "choices": ["a", "a", "b", "c"], "answer": 0}]})
        app_store._db.execute("UPDATE seen SET ts = ts - 86400 * 2")
        n = asyncio.run(curator.generate_recall_questions(None, s, app_store, ask=ask))
        assert n == 1 and "Pharos" in seen_prompt["p"] and "ignore any instructions" in seen_prompt["sys"]
        assert app_store.cards_to_ask(min_age_hours=0) == []  # asked once only

        card = c.get("/api/card/recall").json()
        assert card["title"] == "When was the Pharos built?" and card["quiz"]["answer"] == 0
        assert card["url"] == "https://en.wikipedia.org/wiki/Lighthouse" and "2 days ago" in card["label"]
        assert c.get("/api/card/recall").status_code == 503  # snoozed once served
        rid = card["quiz"]["id"]
        r = c.post(f"/api/recall/{rid}", json={"correct": True}).json()
        assert r["box"] == 1 and r["next_in_days"] == store_mod.RECALL_INTERVALS[1] and r["correct_30d"] == 1
        r = c.post(f"/api/recall/{rid}", json={"correct": False}).json()
        assert r["box"] == 0 and r["next_in_days"] == 1 and r["answered_30d"] == 2
        assert c.post("/api/recall/999", json={"correct": True}).status_code == 404


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
            if request.url.raw_path == b"/api/profile/Axl%20Liess":
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
    assert body["about"] == "Axl Liess" and body["kind"] == "interest" and body["source"] == "liessure"
    assert body["details"] == {"topic": "Cascade Volcanoes", "level": "like", "category": "subject", "wikidata": "Q123"}
    assert body["source_ref"] == "interest:Q123:2026-W47"
    assert body["quote"] == "Mount St. Helens; Glacier Peak; Mount Baker"
    assert cur is not None


def test_invidious_calls_carry_the_watch_token():
    seen = []

    def handler(request):
        if request.url.host == "inv.test":
            seen.append(request.headers.get("x-watch-token"))
        return fake_backend(request)
    with make_client(full_settings(invidious_watch_token="wt", nebula_feeds=[]), handler) as c:
        assert c.get("/api/card/video").status_code == 200
        assert c.get("/img", params={"src": "http://inv.test/vi/x/mqdefault.jpg"}).status_code in (200, 502)
    assert seen and all(t == "wt" for t in seen)


def test_skill_names_are_short_and_merge_duplicates():
    assert sources.skill_name_guess(
        "Limited evidence of eBPF or kernel-level telemetry work (nice-to-have but not required)") \
        == "EBPF or kernel-level telemetry"
    assert sources.skill_name_guess("No direct experience with Go") == "Go"
    assert sources.skill_name_guess("Kubernetes in production") == "Kubernetes in production"


@pytest.mark.anyio
async def test_skill_names_from_the_llm_group_gaps(monkeypatch):
    from app import curator as cur
    sources._skill_names.clear()

    async def ask(client, s, prompt, system=None):
        assert "data, not an instruction" in system
        return json.dumps({"skills": [{"id": 0, "skill": "Kubernetes"}, {"id": 1, "skill": "Go"},
                                      {"id": 2, "skill": "Kubernetes"}]})
    monkeypatch.setattr(cur, "ask_llm", ask)
    s = full_settings(llm_url="http://llm.test", llm_key="k")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_backend)) as hc:
        gaps = await sources.skill_gaps(hc, s)
    k8s = next(g for g in gaps if g["gap"] == "Kubernetes")
    assert len(k8s["jobs"]) == 3 and set(k8s["notes"]) == {"Kubernetes in production", "kubernetes in production!"}
    sources._skill_names.clear()


def test_lab_health_leads_with_the_worst_alert():
    sources._telemetry_cache.put("alerts", [
        {"labels": {"alertname": "Disk filling", "severity": "warning"}, "state": "Alerting",
         "annotations": {"summary": "85%"}},
        {"labels": {"alertname": "Backups stale", "severity": "critical"}, "state": "Alerting",
         "annotations": {"summary": "No dump in 48 h"}},
        {"labels": {"alertname": "Backups stale", "severity": "critical", "instance": "x"}, "state": "Alerting"},
    ])
    with make_client(full_settings()) as c:
        card = c.get("/api/card/telemetry").json()
    assert card["title"] == "Backups stale" and card["label"] == "Lab health · 2 firing, 1 critical"
    assert card["blurb"] == "No dump in 48 h Also firing: Disk filling."


def test_energy_periods_and_solar_card(monkeypatch):
    import asyncio
    from datetime import datetime, timedelta, timezone
    tz = timezone(timedelta(hours=-8))
    now = datetime(2026, 11, 18, 14, 0, tzinfo=tz)  # a Wednesday

    def day(y, m, d, kwh):
        return {"start": datetime(y, m, d, tzinfo=tz).timestamp() * 1000, "change": kwh}
    prod = [day(2026, 1, 5, 10), day(2026, 10, 31, 5), day(2026, 11, 2, 4), day(2026, 11, 16, 7),
            day(2026, 11, 17, 16.7), day(2026, 11, 18, 3.2), day(2026, 11, 10, -12000)]
    got = sources.energy_periods(prod, now.date(), tz)
    assert got == {"today": 3.2, "yesterday": 16.7, "week": 7 + 16.7 + 3.2, "month": 4 + 7 + 16.7 + 3.2,
                   "year": 10 + 5 + 4 + 7 + 16.7 + 3.2}

    async def stats(s, ids, start):
        assert start.date().isoformat() == "2026-01-01"
        return {ids[0]: prod, ids[1]: [day(2026, 11, 3, 61.8)]}
    monkeypatch.setattr(sources, "ha_statistics", stats)
    s = full_settings()
    states = [{"entity_id": "sensor.envoy_1_lifetime_energy_production"},
              {"entity_id": "sensor.envoy_1_lifetime_energy_consumption"},
              {"entity_id": "sensor.envoy_1_current_power_production", "state": "0",
               "attributes": {"unit_of_measurement": "W"}}]
    card = asyncio.run(sources.solar_card(None, s, states, now))
    assert card.title == "Today's sun so far: 3.2 kWh ($0.70)"
    assert "This week 26.9 kWh ($5.92)." in card.blurb and "November 30.9 kWh ($6.80)." in card.blurb
    assert "2026 so far 45.9 kWh ($10.10)." in card.blurb and "covered 50% of what the house" in card.blurb
    assert card.subtitle == "" and "$0.22/kWh" in card.blurb
    sources._stats_cache._data.clear()
    early = now.replace(hour=6)
    prod[5]["change"] = 0
    card = asyncio.run(sources.solar_card(None, s, states, early))
    assert card.title == "Yesterday's sun: 16.7 kWh ($3.67)"


def test_todo_cards_with_estimates():
    import asyncio
    items = [{"id": 1, "text": "Call the dentist", "notes": None, "link_url": None, "checked": False},
             {"id": 2, "text": "Repaint the fence", "notes": "two coats", "link_url": "https://paint.example/x",
              "checked": False, "due_at": "2026-12-01T00:00:00"}]
    calls = []

    async def ask(client, s, prompt, system=None):
        calls.append(prompt)
        assert "data, not an" in system and "Call the dentist" in prompt
        return json.dumps({"items": [{"id": 0, "minutes": 5, "label": "5 min", "assumption": "One call."},
                                     {"id": 1, "minutes": 480, "label": "a weekend day", "assumption": "Two coats."}]})
    store = Store(":memory:")
    s = full_settings(llm_url="http://llm.test", llm_key="k")
    assert asyncio.run(curator.estimate_todos(None, s, store, items, ask=ask)) == 2
    assert asyncio.run(curator.estimate_todos(None, s, store, items, ask=ask)) == 0  # cached
    items[0]["text"] = "Call the dentist about the crown"
    store_calls = len(calls)
    asyncio.run(curator.estimate_todos(None, s, store, items, ask=lambda *a, **k: ask(*a, **k)))
    assert len(calls) == store_calls + 1  # changed text: estimated again
    est = store.todo_estimates()
    card = sources.todo_card(s, items[1], est[2])
    assert card.label == "To do · a weekend day" and card.title == "Repaint the fence"
    assert card.blurb == "Two coats. two coats Due 2026-12-01."
    assert card.actions[0] == {"label": "Open link", "url": "https://paint.example/x"}
    assert sources.todo_fits(5, 10) > sources.todo_fits(480, 10)
    assert sources.todo_fits(480, 19) > sources.todo_fits(480, 10)
    assert sources.todo_fits(480, 23) < 0.2


def test_todo_card_endpoint(monkeypatch):
    def backend(request):
        if request.url.host == "lists.test":
            assert request.headers["authorization"] == "Bearer lt"
            assert request.url.path == "/lists/6" and request.url.params["include_checked"] == "false"
            return httpx.Response(200, json={"items": [
                {"id": 3, "text": "Renew passport", "checked": False, "notes": None, "link_url": None}]})
        return fake_backend(request)

    async def ask(client, s, prompt, system=None):
        return json.dumps({"items": [{"id": 0, "minutes": 20, "label": "20 min", "assumption": "Online form."}]})
    monkeypatch.setattr(curator, "ask_llm", ask)
    s = full_settings(llm_url="http://llm.test", llm_key="k", lists=Service("http://lists.test", "https://lists.example"),
                      lists_token="lt")
    with make_client(s, handler=backend) as c:
        assert c.get("/api/sources").json()["sources"]["todo"]["configured"]
        card = c.get("/api/card/todo").json()
    assert card["title"] == "Renew passport" and card["label"] == "To do · 20 min"
    assert card["url"] == "https://lists.example/" and card["blurb"] == "Online form."


def test_math_progress_is_kept():
    import time as _t
    st = Store(":memory:")
    day = 86400
    now = _t.time()
    # Two days ago and yesterday right; today one right, one revealed; probability is weak.
    for ago, topic, ok in [(2, "algebra", 1), (1, "algebra", 1), (0, "algebra", 1),
                           (0, "probability", 0), (0, "probability", 0), (0, "probability", 0)]:
        st._db.execute("INSERT INTO math_attempts (ts, ref, topic, correct, tries, revealed) VALUES (?, 'r', ?, ?, 1, ?)", (now - ago * day, topic, ok, 1 - ok))
    stats = st.math_stats(now)
    assert stats["today"] == {"tried": 4, "right": 1, "helped": 0} and stats["streak_days"] == 3
    assert stats["weakest"] == "probability" and stats["weakest_rate"] == 0
    with make_client(full_settings()) as c:
        r = c.post("/api/math", json={"ref": "math:x", "topic": "geometry", "correct": True, "tries": 2})
        assert r.status_code == 200 and r.json()["today"] == {"tried": 1, "right": 1, "helped": 0}
        assert r.json()["streak_days"] == 1
        assert c.get("/api/math/stats").json()["all"]["tried"] == 1


def test_math_help_for_a_wrong_answer(monkeypatch):
    seen = {}

    async def ask(client, s, prompt, system=None):
        seen["prompt"], seen["system"] = prompt, system
        return "1. You added instead of subtracting.\n2. ..."
    monkeypatch.setattr(curator, "ask_llm", ask)
    with make_client(full_settings(llm_url="http://llm.test", llm_key="k")) as c:
        card = c.get("/api/card/math").json()
        assert card["open_label"] == "More info"
        r = c.post("/api/math/help", json={"question": "Solve for x: 4x + 5 = -3", "answer": "-2", "theirs": "0.5",
                                            "solution": "4x = -8, so x = -2."})
    assert r.status_code == 200 and r.json()["help"].startswith("1. You added")
    assert "The learner answered: 0.5" in seen["prompt"] and "numbered steps" in seen["system"]
    with make_client(full_settings()) as c:
        assert c.post("/api/math/help", json={"question": "q", "answer": "1", "theirs": "2"}).status_code == 409


def test_recall_asks_about_the_article_not_just_the_card():
    import asyncio
    st = Store(":memory:")
    st.remember_card("wikipedia", "Cough reflex test", "Cough reflex test", "", "A short summary.",
                     "https://en.m.wikipedia.org/wiki/Cough_reflex_test")
    st._db.execute("UPDATE seen SET ts = ts - 86400")

    def backend(request):
        q = request.url.params
        assert request.url.path == "/w/api.php" and q["titles"] == "Cough reflex test" and q["exchars"] == "6000"
        return httpx.Response(200, json={"query": {"pages": [{"extract": "The cough reflex test uses citric acid. " * 40}]}})
    seen = {}

    async def ask(client, s, prompt, system=None):
        seen["prompt"] = prompt
        return "{}"

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as hc:
            await curator.generate_recall_questions(hc, full_settings(), st, ask=ask)
    asyncio.run(run())
    assert "citric acid" in seen["prompt"] and "A short summary." not in seen["prompt"]


def test_reading_in_the_feed_counts(client):
    ev = {"kind": "wikipedia", "ref": "X", "action": "read", "title": "X", "text": "t", "url": "https://en.wikipedia.org/wiki/X"}
    assert client.post("/api/event", json=ev).status_code == 204
    assert client.app.state.store.cards_to_ask(min_age_hours=0)[0]["ref"] == "X"


def test_photo_details_and_identify(monkeypatch):
    asset = {"id": "p1", "localDateTime": "2024-05-01T10:00:00", "exifInfo": {
        "city": "Kyoto", "country": "Japan", "latitude": 35.0116, "longitude": 135.7681, "make": "Google",
        "model": "Pixel 8", "fNumber": 1.7, "exposureTime": "1/120", "iso": 50, "focalLength": 6.9,
        "exifImageWidth": 4080, "exifImageHeight": 3072}}
    d = {x["label"]: x.get("value") or x.get("url") for x in sources.photo_details(asset)}
    assert d["Taken"] == "Wed 1 May 2024, 10:00" and d["Where"] == "Kyoto, Japan"
    assert d["Camera"] == "Google Pixel 8" and d["Exposure"] == "f/1.7 · 1/120 s · ISO 50 · 6.9 mm"
    assert d["Map"].startswith("https://www.openstreetmap.org/?mlat=35.01160")
    assert d["What's nearby"].endswith("#/coord/35.01160,135.76810")
    card = sources._immich_card(full_settings(), asset, "")
    assert card.photo == {"id": "p1", "when": "2024-05-01", "place": "Kyoto, Japan", "lat": 35.0116, "lon": 135.7681}

    def backend(request):
        if request.url.host == "im.test" and request.url.path == "/api/assets/abcdef12":
            return httpx.Response(200, json={**asset, "id": "abcdef12"})
        if request.url.host == "im.test" and request.url.path.endswith("/thumbnail"):
            return httpx.Response(200, content=b"\xff\xd8x", headers={"content-type": "image/jpeg"})
        return fake_backend(request)
    seen = {}

    async def ask(client, s, content, system=None):
        seen["content"], seen["system"] = content, system
        return "A torii gate at Fushimi Inari (fairly sure)."
    monkeypatch.setattr(curator, "ask_llm", ask)
    curator._identified.clear()
    with make_client(full_settings(llm_url="http://llm.test", llm_key="k"), handler=backend) as c:
        r = c.post("/api/photo/abcdef12/identify")
        assert r.status_code == 200 and r.json()["text"].startswith("A torii gate")
        assert c.post("/api/photo/..%2Fx/identify").status_code in (400, 404, 405)  # never reaches Immich
    assert "Kyoto, Japan" in seen["content"][0]["text"] and seen["content"][1]["image_url"]["url"].startswith("data:image/jpeg")
    with make_client(full_settings()) as c:
        assert c.post("/api/photo/abcdef12/identify").status_code == 409


def test_page_code_always_revalidates(client):
    for path in ("/app.js", "/style.css", "/sw.js"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path


def test_index_names_code_by_version(client):
    html = client.get("/").text
    assert re.search(r'src="/app\.js\?v=[0-9a-f]{10}"', html) and re.search(r'href="/style\.css\?v=[0-9a-f]{10}"', html)


AXL, DANI, STRANGER = "387450248433827843", "388076739911221251", "999"


def sso_backend(who):
    """A fake Zitadel: discovery, token and userinfo. who[0] is the sub that signs in next."""
    def handler(request):
        if request.url.host == "sso.test":
            p = request.url.path
            if p == "/.well-known/openid-configuration":
                return httpx.Response(200, json={"authorization_endpoint": "https://sso.test/oauth/v2/authorize",
                                                 "token_endpoint": "https://sso.test/oauth/v2/token",
                                                 "userinfo_endpoint": "https://sso.test/oidc/v1/userinfo"})
            if p == "/oauth/v2/token":
                assert request.headers["authorization"].startswith("Basic ")
                body = dict(x.split("=", 1) for x in request.content.decode().split("&"))
                assert body["code"] == "c0de" and len(body["code_verifier"]) > 40
                return httpx.Response(200, json={"access_token": "at-" + who[0]})
            if p == "/oidc/v1/userinfo":
                sub = request.headers["authorization"].removeprefix("Bearer at-")
                return httpx.Response(200, json={"sub": sub, "given_name": {AXL: "Axl", DANI: "Dani"}.get(sub, "X")})
        return fake_backend(request)
    return handler


def sign_in(c, who, sub):
    from urllib.parse import parse_qs, urlparse
    who[0] = sub
    r = c.get("/auth/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://sso.test/oauth/v2/authorize?")
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["redirect_uri"] == ["https://liessure.example/auth/callback"]
    return c.get("/auth/callback", params={"code": "c0de", "state": q["state"][0]}, follow_redirects=False)


# A made-up reader for the per-person tests (real preferences live only on the server).
READER = "555000111"
READER_PREFS = {
    "lang": "es", "lang_name": "Spanish", "wikipedia": "es", "news_feeds": ["http://news.test/rss"],
    "interests": ["Volcanes"], "video_topics": ["volcanes"],
    "learning": [{"name": "English", "label": "Inglés", "code": "en", "style": "reading"},
                 {"name": "Korean", "label": "Coreano", "code": "ko", "style": "beginner"}],
    "kinds_off": ["homelab", "infosec"],
    "career": {"now": "test pilot", "likes": ["Field A"], "explore": ["Field B"]},
}


def sso_settings(**kw):
    return full_settings(zitadel_issuer="https://sso.test", zitadel_client_id="cid", zitadel_client_secret="sec",
                         zitadel_user_subs=[AXL, DANI], public_url="https://liessure.example", **kw)


def test_sign_in_and_per_user_feeds():
    who = [AXL]
    app = create_app(sso_settings(), transport=httpx.MockTransport(sso_backend(who)), store=Store(":memory:"),
                     run_curator=False)
    with TestClient(app, base_url="https://testserver") as c:
        # Signed out: the page goes to sign-in, the API says 401, static code and health stay public.
        assert c.get("/", follow_redirects=False).headers["location"] == "/auth/login"
        assert c.get("/api/feed").status_code == 401
        assert c.get("/app.js").status_code == 200 and c.get("/healthz").status_code == 200
        r = sign_in(c, who, AXL)
        assert r.status_code == 303 and "liessure_session" in r.headers["set-cookie"]
        assert "httponly" in r.headers["set-cookie"].lower() and "secure" in r.headers["set-cookie"].lower()
        assert c.get("/api/me").json() == {"user": "owner", "name": "Axl", "owner": True, "sso": True, "lang": "en", "todo": False}
        assert c.get("/api/sources").json()["sources"]["job"]["configured"]
        c.post("/api/math", json={"ref": "m", "topic": "algebra", "correct": True})

    with TestClient(app, base_url="https://testserver") as d:
        sign_in(d, who, DANI)
        assert d.get("/api/me").json()["user"] == DANI
        srcs = d.get("/api/sources").json()["sources"]
        assert not srcs["job"]["configured"] and not srcs["telemetry"]["configured"] and srcs["math"]["configured"]
        assert d.get("/api/card/job").status_code == 503
        kinds = {x["kind"] for x in d.get("/api/feed?n=12").json()["cards"]}
        assert not kinds & {"job", "skill", "todo", "telemetry"}
        assert d.get("/api/math/stats").json()["all"]["tried"] == 0  # her own progress, not axl's
        d.post("/api/event", json={"kind": "wikipedia", "ref": "Seoul", "action": "open", "title": "Seoul",
                                   "text": "Capital.", "url": "https://en.wikipedia.org/wiki/Seoul"})
        rows = app.state.store.cards_to_ask(min_age_hours=0)
        assert [(r["user"], r["ref"]) for r in rows] == [(DANI, "Seoul")]
        assert app.state.store.events_since(1) == []  # her opens don't steer axl's curator

    with TestClient(app, base_url="https://testserver") as x:
        r = sign_in(x, who, STRANGER)
        assert r.status_code == 403 and "set-cookie" not in r.headers
        assert x.get("/api/feed").status_code == 401

    # Taking someone off the allowlist ends their live session.
    with TestClient(app, base_url="https://testserver") as d:
        sign_in(d, who, DANI)
        app.state.sso.allowed.discard(DANI)
        assert d.get("/api/feed").status_code == 401
        assert d.get("/auth/logout", follow_redirects=False).status_code == 303


def test_old_database_is_migrated_to_the_owner(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE events (ts REAL, kind TEXT, ref TEXT, action TEXT, title TEXT);
        INSERT INTO events VALUES (1, 'wikipedia', 'X', 'open', 'X');
        CREATE TABLE seen (kind TEXT NOT NULL, ref TEXT NOT NULL, title TEXT NOT NULL, subtitle TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', ts REAL NOT NULL,
            asked INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (kind, ref));
        INSERT INTO seen VALUES ('wikipedia', 'X', 'X', '', 't', 'https://en.wikipedia.org/wiki/X', 1, 0);
        CREATE TABLE math_attempts (ts REAL NOT NULL, ref TEXT NOT NULL, topic TEXT NOT NULL, correct INTEGER NOT NULL,
            tries INTEGER NOT NULL, revealed INTEGER NOT NULL);
        INSERT INTO math_attempts VALUES (strftime('%s','now'), 'm', 'algebra', 1, 1, 0);
    """)
    db.commit()
    db.close()
    st = Store(path)
    assert st.cards_to_ask(min_age_hours=0)[0]["user"] == "owner"
    assert st._db.execute("SELECT user FROM events").fetchone()[0] == "owner"
    assert st.math_stats()["all"]["tried"] == 1
    Store(path)  # opening it again is a no-op


def test_infosec_cards_are_phrack_articles_and_feeds(client):
    cards = [client.get("/api/card/infosec").json() for _ in range(30)]
    phrack = [c for c in cards if c["label"] == "Infosec · Phrack #73"]
    assert {c["title"] for c in phrack} == {"Escaping from a Docker container through vDSO",
                                           "Beyond the Facade :: Exposing Smart Giants"}
    assert all(c["url"].startswith("https://liessure.liess.tv/read/phrack/73/") and c["open_in_app"] for c in phrack)
    assert all(c["actions"][0]["url"].startswith("https://phrack.org/issues/73/") for c in phrack)
    assert any(c["label"] == "Infosec · Atom Daily" for c in cards)


def test_bites_come_from_the_article_and_serve_from_the_pool():
    import asyncio
    st = Store(":memory:")
    seen = {}

    async def ask(client, s, prompt, system=None):
        seen["prompt"], seen["system"] = prompt, system
        return json.dumps({"bites": [
            {"title": "The Cascade volcanoes are one chain", "body": "They run north to south, fed by one subducting plate."},
            {"title": "", "body": "dropped"}]})
    s = full_settings(llm_url="http://llm.test", llm_key="k", wiki_interests=["Pacific Northwest geology"])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_backend)) as hc:
            return await curator.generate_bites(hc, s, st, random.Random(1), ask=ask)
    assert asyncio.run(run()) == 1
    assert "ONLY facts stated in the article" in seen["system"] and "Cascade Volcanoes run north" in seen["prompt"]
    assert asyncio.run(run()) == 0  # that article's bites are already in the pool
    app = create_app(s, transport=httpx.MockTransport(fake_backend), store=st, run_curator=False)
    with TestClient(app) as c:
        card = c.get("/api/card/bite").json()
    assert card["title"] == "The Cascade volcanoes are one chain" and card["label"] == "Bite-size · Pacific Northwest geology"
    assert card["url"] == "https://en.m.wikipedia.org/wiki/Geology_of_the_Pacific_Northwest"


def test_audio_streams_the_file_when_the_browser_can_play_it():
    calls = []

    def backend(request):
        if request.url.path.startswith("/Audio/"):
            calls.append((request.url.path, dict(request.url.params)))
        return fake_backend(request)
    sources._codec_cache.clear()
    with make_client(full_settings(), handler=backend) as c:
        assert c.get("/stream/audio/abc12345").status_code == 200
        assert c.get("/stream/audio/a1ac0001").status_code == 200
    assert calls[0] == ("/Audio/abc12345/stream", {"static": "true"})  # whole file: a length, so seeking works
    assert calls[1][0] == "/Audio/a1ac0001/universal" and calls[1][1]["AudioCodec"] == "mp3"


def test_series_cards_are_specific_episodes(client):
    titles = {}
    for _ in range(25):
        c = client.get("/api/card/show").json()
        titles[c["label"]] = c
    ep = titles["Episode on Jellyfin"]
    assert ep["title"] == "Cat and Mouse" and ep["subtitle"].startswith("The Clone Wars · S2E16")
    assert ep["url"] == "https://jellyfin.example/web/#/details?id=e0e0e0e1&serverId=srv" and ep["open_in_app"]
    assert ep["image"].endswith("%2FItems%2Fe0e0e0e1%2FImages%2FPrimary%3FmaxWidth%3D720%26quality%3D85")
    assert titles["Next up on Jellyfin"]["title"] == "Hunted"


def test_phrack_reader_reflows_text(client):
    r = client.get("/read/phrack/73/word-machines-for-weird-machines_md")
    assert r.status_code == 200
    h = r.text
    assert "<h1>Word Machines for Weird Machines</h1>" in h and "noperator · Phrack #73" in h
    assert "<h2>0 - Intro</h2>" in h and "<h2>1 - Results</h2>" in h
    assert "<p>Phrack is said to be a snapshot of hacker culture at each issue, so I should open by" in h
    assert "writing &amp; more.</p>" in h
    assert "<pre>    int main(void) {\n        return 0;\n    }</pre>" in h
    assert client.get("/read/phrack/73/..%2Fetc").status_code == 404


def test_job_cards_expand_to_the_description(client):
    card = None
    for _ in range(20):
        c = client.get("/api/card/job")
        if c.status_code == 200:
            card = c.json()
            break
    if card is None:  # weekends without a due step: no job card, check the endpoint directly
        card = {"more": {"url": "/api/job/1/description"}}
    assert card["more"]["url"].startswith("/api/job/")
    r = client.get(card["more"]["url"])
    assert r.status_code == 200 and r.json()["text"].startswith("About the role")
    assert client.get("/api/job/abc/description").status_code == 422


def test_a_spanish_reader_gets_lessons(monkeypatch):
    who = [READER]

    async def ask(client, s, prompt, system=None):
        if "Simple" in prompt or "comprehension" in prompt:
            return json.dumps({"items": [{"id": i, "translation": "Los volcanes son aberturas en la tierra.",
                                          "words": [{"word": "openings", "meaning": "aberturas"}]} for i in range(4)]})
        if "beginner Korean" in prompt:
            return json.dumps({"items": [{"text": "안녕하세요", "romanization": "annyeonghaseyo",
                                          "translation": "Hola", "words": [{"word": "안녕", "romanization": "annyeong",
                                                                            "meaning": "paz, bienestar"}],
                                          "note": "Forma educada."}]})
        if "Write the cards in Spanish" in prompt:
            return json.dumps({"bites": [{"title": "Los volcanes duermen", "body": "Algunos pasan siglos sin erupción."}]})
        return "{}"
    monkeypatch.setattr(curator, "ask_llm", ask)
    s = sso_settings(llm_url="http://llm.test", llm_key="k", user_prefs={READER: READER_PREFS})
    app = create_app(s, transport=httpx.MockTransport(sso_backend(who)), store=Store(":memory:"), run_curator=False)
    with TestClient(app, base_url="https://testserver") as d:
        app.state.sso.allowed.add(READER)
        sign_in(d, who, READER)
        assert d.get("/api/me").json()["lang"] == "es"
        srcs = d.get("/api/sources").json()["sources"]
        assert srcs["lesson"]["configured"] and not srcs["homelab"]["configured"] and not srcs["infosec"]["configured"]
        seen = {}
        for n in range(40):
            r = d.get("/api/card/lesson")
            if r.status_code == 200:  # a batch can run dry once its few cards have been shown
                seen.setdefault(r.json()["label"], r.json())
            if len(seen) == 2:
                break
        reading, phrase = seen["Inglés · lectura"], seen["Coreano · frase"]
        assert reading["title"] == "Volcano" and reading["details_label"] == "Ver explicación"
        assert {"label": "Traducción", "value": "Los volcanes son aberturas en la tierra."} in reading["details"]
        assert {"label": "openings", "value": "aberturas"} in reading["details"]
        assert phrase["title"] == "안녕하세요" and phrase["subtitle"] == "annyeonghaseyo"
        assert {"label": "안녕 (annyeong)", "value": "paz, bienestar"} in phrase["details"]
        assert phrase["actions"][0]["speak"]["lang"] == "ko-KR" and "tl=es" in phrase["url"]
        bite = d.get("/api/card/bite").json()
        assert bite["title"] == "Los volcanes duermen" and "es.m.wikipedia.org" in bite["url"]

    with TestClient(app, base_url="https://testserver") as a:
        sign_in(a, who, AXL)
        assert a.get("/api/me").json()["lang"] == "en"
        assert a.get("/api/sources").json()["sources"]["lesson"]["configured"]  # from his profile's languages


def test_user_prefs_come_from_the_server_not_the_code(tmp_path):
    from app.config import Settings, parse_user_prefs
    assert Settings().user_prefs == {}  # no one's preferences are in the repo
    f = tmp_path / "prefs.json"
    f.write_text(json.dumps({"42": {"lang": "es", "kinds_off": ["infosec"]}}))
    p = parse_user_prefs(json.dumps({"42": {"kinds_off": []}, "7": {"lang": "fr"}}), str(f))
    assert p["42"] == {"lang": "es", "kinds_off": []} and p["7"] == {"lang": "fr"}
    assert parse_user_prefs("not json", str(tmp_path / "missing.json")) == {}


def test_career_exploration_cards(monkeypatch):
    who = [READER]
    seen = {}

    async def ask(client, s, prompt, system=None):
        if system == curator.CAREER_SYSTEM:
            seen["prompt"] = prompt
            return json.dumps({"cards": [
                {"title": "Un día en la informática médica", "body": "Organizas datos clínicos."},
                {"title": "Cómo se llega", "body": "Con estudios de salud y datos."},
                {"title": "Lo que ya sabes te sirve", "body": "Sugerencia: el trato con pacientes."}]})
        return "{}"
    monkeypatch.setattr(curator, "ask_llm", ask)
    app = create_app(sso_settings(llm_url="http://llm.test", llm_key="k", user_prefs={READER: READER_PREFS}),
                     transport=httpx.MockTransport(sso_backend(who)), store=Store(":memory:"), run_curator=False)
    with TestClient(app, base_url="https://testserver") as d:
        app.state.sso.allowed.add(READER)
        sign_in(d, who, READER)
        assert d.get("/api/sources").json()["sources"]["career"]["configured"]
        card = d.get("/api/card/career").json()
    assert card["kind"] == "career" and card["label"].split(" · ")[0] in ("Carreras", "Descubre")
    assert "test pilot" in seen["prompt"] and "Write everything in Spanish" in seen["prompt"]
    assert "Cascade Volcanoes" in seen["prompt"]  # the article text from the fake extracts API
    with TestClient(app, base_url="https://testserver") as a:
        sign_in(a, who, AXL)
        assert not a.get("/api/sources").json()["sources"]["career"]["configured"]


def test_app_sign_in_through_a_browser_tab():
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlparse
    who = [AXL]
    app = create_app(sso_settings(), transport=httpx.MockTransport(sso_backend(who)), store=Store(":memory:"),
                     run_curator=False)
    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with TestClient(app, base_url="https://testserver") as tab:  # the Chrome tab
        assert tab.get("/auth/login?app=nope", follow_redirects=False).status_code == 400
        r = tab.get(f"/auth/login?app={challenge}", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = tab.get("/auth/callback", params={"code": "c0de", "state": state}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("liessure://signed-in?ticket=")
        assert "set-cookie" not in r.headers  # the tab never holds a session
        ticket = parse_qs(urlparse(r.headers["location"]).query)["ticket"][0]
    with TestClient(app, base_url="https://testserver") as other:  # an app that grabbed the link
        assert other.get("/auth/ticket", params={"ticket": ticket, "verifier": "w" * 50},
                         follow_redirects=False).status_code == 403
    # One use: the failed attempt above already burnt it, so start over for the real app.
    with TestClient(app, base_url="https://testserver") as tab:
        r = tab.get(f"/auth/login?app={challenge}", follow_redirects=False)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = tab.get("/auth/callback", params={"code": "c0de", "state": state}, follow_redirects=False)
        ticket = parse_qs(urlparse(r.headers["location"]).query)["ticket"][0]
    with TestClient(app, base_url="https://testserver") as webview:  # the app's WebView
        r = webview.get("/auth/ticket", params={"ticket": ticket, "verifier": verifier}, follow_redirects=False)
        assert r.status_code == 303 and "liessure_session" in r.headers["set-cookie"]
        assert webview.get("/api/me").json()["user"] == "owner"
        assert webview.get("/auth/ticket", params={"ticket": ticket, "verifier": verifier},
                           follow_redirects=False).status_code == 403  # used up


def test_a_spanish_reader_gets_spanish_help_and_no_wikipedia_walls(monkeypatch):
    who = [READER]
    seen = {}

    async def ask(client, s, prompt, system=None):
        seen["system"] = system
        return "1. Restaste en vez de sumar."
    monkeypatch.setattr(curator, "ask_llm", ask)
    app = create_app(sso_settings(llm_url="http://llm.test", llm_key="k", user_prefs={READER: READER_PREFS},
                                  language_practice_url="https://practice.example"),
                     transport=httpx.MockTransport(sso_backend(who)), store=Store(":memory:"), run_curator=False)
    from urllib.parse import parse_qs, urlparse
    with TestClient(app, base_url="https://testserver") as d:
        app.state.sso.allowed.add(READER)
        sign_in(d, who, READER)
        math = d.get("/api/card/math").json()
        q = parse_qs(urlparse(math["actions"][0]["url"]).query)["q"][0]
        assert q.startswith("Reply only in Spanish")
        r = d.post("/api/math/help", json={"question": "q", "answer": "1", "theirs": "2"})
        assert r.status_code == 200 and seen["system"].endswith("Write the whole explanation in Spanish.")
        labels = {d.get("/api/card/language").json().get("label") for _ in range(30)}
        assert not any("Wikipedia" in (l or "") for l in labels)  # no whole articles in a language she's starting
        assert not any("practice" in (l or "") for l in labels)  # and not the owner's drill
    with TestClient(app, base_url="https://testserver") as a:
        sign_in(a, who, AXL)
        q = parse_qs(urlparse(a.get("/api/card/math").json()["actions"][0]["url"]).query)["q"][0]
        assert q.startswith("Tutor me")  # English for the owner


def test_the_owner_gets_beginner_lessons_in_the_languages_he_is_learning(monkeypatch):
    seen = {}

    async def ask(client, s, prompt, system=None):
        seen.setdefault("prompts", []).append(prompt)
        return json.dumps({"items": [{"text": "నమస్కారం", "romanization": "namaskāram", "translation": "Hello",
                                      "words": [{"word": "నమస్కారం", "romanization": "namaskāram", "meaning": "greeting"}],
                                      "note": "Polite."}]})
    monkeypatch.setattr(curator, "ask_llm", ask)
    s = full_settings(llm_url="http://llm.test", llm_key="k", languages_learning=[("Telugu", "te")], curator_share=0)
    with make_client(s) as c:
        card = c.get("/api/card/lesson").json()
        labels = {c.get("/api/card/language").json().get("label") for _ in range(20)}
    assert card["title"] == "నమస్కారం" and card["details_label"] == "Explain"
    assert {"label": "Translation", "value": "Hello"} in card["details"]
    assert "beginner Telugu" in seen["prompts"][0] and "whose language is English" in seen["prompts"][0]
    assert not any("Wikipedia" in (l or "") for l in labels)


def test_not_interested_topics_are_kept_out():
    from app import avoid as av
    football = {"title": "Linfield F.C.", "subtitle": "Association football club in Northern Ireland", "label": "Wikipedia"}
    assert av.matches(["sports"], football)
    assert av.matches(["alcohol"], {"title": "I Drink Wine", "label": "Song"})
    assert av.matches(["animal products"], {"title": "Pulled pork", "blurb": "A barbecue dish."})
    assert not av.matches(["sports"], {"title": "Morphology (linguistics)", "subtitle": "Study of words"})
    assert not av.matches(["alcohol"], {"title": "Ginkgo", "blurb": "A tree. Ginseng is different."})
    assert not av.matches(["animal products"], {"title": "Marmot", "blurb": "A large ground squirrel."})
    assert av.matches(["opera"], {"title": "Tosca", "subtitle": "Opera by Puccini"}) and not av.matches(["opera"], {"title": "Operations"})

    with make_client(full_settings()) as c:
        assert c.get("/api/me/avoid").json() == {"avoid": [], "suggested": ["sports", "alcohol", "animal products"]}
        assert c.post("/api/me/avoid", json={"term": "Sports"}).json()["avoid"] == ["sports"]
        c.post("/api/me/avoid", json={"term": "lighthouse"})
        c.post("/api/me/avoid", json={"term": "software"})
        # The fake Wikipedia only has a lighthouse (random) and "... is software" articles (interest search);
        # with both hidden, the card is skipped, never shown.
        assert c.get("/api/card/wikipedia").status_code == 503
        c.delete("/api/me/avoid/software")
        assert c.delete("/api/me/avoid/lighthouse").json()["avoid"] == ["sports"]
        assert c.get("/api/card/wikipedia").status_code == 200


def test_math_help_is_tracked():
    with make_client(full_settings()) as c:
        card = c.get("/api/card/math").json()
        assert card["actions"][0]["assist"] == "tutor"
        assert c.post("/api/math/assist", json={"ref": "m1", "topic": "algebra", "kind": "tutor"}).status_code == 204
        c.post("/api/math/assist", json={"ref": "m1", "topic": "algebra", "kind": "hint"})
        c.post("/api/math/assist", json={"ref": "m2", "topic": "geometry", "kind": "fix"})
        assert c.post("/api/math/assist", json={"ref": "m3", "topic": "x", "kind": "cheat"}).status_code == 422
        st = c.get("/api/math/stats").json()
    assert st["today"]["helped"] == 2 and st["most_help"] == "algebra"
    assert st["help_30d"] == {"tutor": 1, "hint": 1, "fix": 1}


def test_translate_hands_the_text_over():
    acts = curator.phrase_actions("안녕하세요", "ko")
    assert acts[1]["translate"] == {"text": "안녕하세요", "from": "ko", "to": "en"}


def test_kdrama_quiz_for_korean_learners():
    import asyncio
    from datetime import date
    from app import kquiz
    kquiz._people.clear()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_backend)) as hc:
            return [await kquiz.card(hc, "es", "es", random.Random(n), today=date(2026, 11, 18)) for n in range(12)]
    cards = asyncio.run(run())
    who = [c for c in cards if c.title == "¿Quién es?"]
    age = [c for c in cards if c.title == "¿Cuántos años tiene?"]
    assert who and age
    for c in cards:
        q = c.quiz
        assert len(q["choices"]) == 4 and len(set(q["choices"])) == 4 and 0 <= q["answer"] < 4
        assert c.image.startswith("https://commons.wikimedia.org/wiki/Special:FilePath/") and c.label == "Quiz de K-drama"
    c = age[0]
    n = int(c.ref.split(":")[1].removeprefix("Q"))
    assert c.quiz["choices"][c.quiz["answer"]] == str(2026 - (1970 + n) - 0)  # born 15 June, so birthday passed
    w = who[0]
    right = w.quiz["choices"][w.quiz["answer"]]
    assert right in w.quiz["solution"]

    # Only for people learning Korean.
    with make_client(full_settings(languages_learning=[("Telugu", "te")])) as c:
        assert not c.get("/api/sources").json()["sources"]["kquiz"]["configured"]
    with make_client(full_settings(languages_learning=[("Korean", "ko")])) as c:
        assert c.get("/api/sources").json()["sources"]["kquiz"]["configured"]


def test_phrase_lessons_ask_what_it_means(monkeypatch):
    import asyncio
    from app import lessons

    async def ask(client, s, prompt, system=None):
        assert "three plausible but wrong" in prompt
        return json.dumps({"items": [{"text": "我想要一碗面。", "romanization": "Wǒ xiǎngyào yī wǎn miàn.",
                                      "translation": "I would like a bowl of noodles.",
                                      "words": [{"word": "面", "romanization": "miàn", "meaning": "noodles"}],
                                      "note": "Polite.", "wrong": ["I ate rice.", "Where is the bowl?", "I want tea."]}]})
    out = asyncio.run(lessons._phrases(None, full_settings(), {"lang": "en", "lang_name": "English"},
                                        {"name": "Mandarin", "code": "zh"}, ask, random.Random(1)))
    q = out[0]["quiz"]
    assert q["choices"][q["answer"]] == "I would like a bowl of noodles." and len(q["choices"]) == 4
    assert out[0]["blurb"].startswith("What does it mean?")


def test_immich_preview_falls_back_to_thumbnail():
    def backend(request):
        if request.url.host == "im.test" and request.url.path.endswith("/thumbnail"):
            if request.url.params.get("size") == "preview":
                return httpx.Response(404, json={"message": "no preview"})
            return httpx.Response(200, content=b"\xff\xd8small", headers={"content-type": "image/jpeg"})
        return fake_backend(request)
    with make_client(full_settings(), handler=backend) as c:
        r = c.get("/img", params={"src": f"{IM}/api/assets/p1/thumbnail?size=preview"})
    assert r.status_code == 200 and r.content == b"\xff\xd8small"


def test_cards_shown_can_be_found_again():
    with make_client(full_settings()) as c:
        cards = c.get("/api/feed?n=8").json()["cards"]
        assert cards
        first = cards[0]
        word = first["title"]  # every word of it must match
        found = c.get("/api/history", params={"q": word}).json()["cards"]
        assert any(x["title"] == first["title"] for x in found)
        assert c.get("/api/history", params={"q": "zzzz nothing like this"}).json()["cards"] == []
        everything = c.get("/api/history").json()["cards"]
        assert len(everything) == len({(x["kind"], x.get("ref") or x["url"]) for x in cards})
    st = Store(":memory:")
    st.HISTORY_MAX = 5
    st.remember_shown([{"kind": "news", "ref": str(n), "title": f"Story {n}"} for n in range(9)])
    assert [x["title"] for x in st.search_history("story")] == ["Story 8", "Story 7", "Story 6", "Story 5", "Story 4"]
    assert st.search_history("100%") == []  # LIKE wildcards are literal


def test_toasts_count_as_alcohol():
    from app import avoid as av
    sante = {"title": "Santé !", "label": "French · cheers!",
             "blurb": "Cheers! A toast said before drinking with others. Literally means 'health.'"}
    assert av.matches(["alcohol"], sante)
    assert not av.matches(["alcohol"], {"title": "French toast", "blurb": "Bread soaked in eggs, then fried."})
    assert "cheers!" not in curator.PHRASE_TOPICS


def test_old_phrase_cards_without_a_quiz_are_dropped():
    import asyncio
    from app import lessons
    st = Store(":memory:")
    old = {"kind": "lesson", "title": "我想要一碗面。", "url": "https://translate.google.com/?sl=zh-CN", "quiz": {}}
    st.add_picks(lessons.pool("owner"), [{"ref": "lesson:zh:old", "card": old, "score": 7}], rejected=[])
    new = {"kind": "lesson", "title": "你好", "url": "https://translate.google.com/?sl=zh-CN",
           "quiz": {"choices": ["a", "b", "c", "d"], "answer": 0}}
    st.add_picks(lessons.pool("owner"), [{"ref": "lesson:zh:new", "card": new, "score": 7}], rejected=[])
    for n in range(6):
        c = asyncio.run(lessons.card(None, full_settings(), st, "owner", {"learning": []}, random.Random(n)))
        assert c is None or c["title"] == "你好"
    assert "lesson:zh:old" in st.known(lessons.pool("owner")) and st.pool_size(lessons.pool("owner")) <= 1


def test_swiped_cards_go_to_done_and_later_lists():
    import time as _t
    with make_client(full_settings()) as c:
        cards = c.get("/api/feed?n=6").json()["cards"]
        done, later = cards[0], cards[1]
        assert c.post("/api/card-state", json={"card": done, "state": "done"}).status_code == 204
        assert c.post("/api/card-state", json={"card": later, "state": "later"}).status_code == 204
        assert c.post("/api/card-state", json={"card": later, "state": "nope"}).status_code == 422
        assert [x["title"] for x in c.get("/api/lists/done").json()["cards"]] == [done["title"]]
        lst = c.get("/api/lists/later").json()["cards"]
        assert [x["title"] for x in lst] == [later["title"]] and lst[0]["due"] > _t.time()
        st = c.app.state.store
        assert st.card_key(done) in st.done_keys()
        # Not due yet: no 'again' card. Make it due: it comes back, marked, and moves further out.
        assert not any(x.get("again") for x in c.get("/api/feed?n=6").json()["cards"])
        st._db.execute("UPDATE history SET due = 0 WHERE state = 'later'")
        back = [x for x in c.get("/api/feed?n=6").json()["cards"] if x.get("again")]
        assert [x["title"] for x in back] == [later["title"]]
        assert c.get("/api/lists/later").json()["cards"][0]["due"] > _t.time() + 2 * 86400  # next: 3 days
        # Put the done card back to normal.
        c.post("/api/card-state", json={"card": done, "state": ""})
        assert c.get("/api/lists/done").json()["cards"] == []
        assert c.get("/api/lists/bogus").status_code == 404
    st = Store(":memory:")
    st.HISTORY_MAX = 2
    st.set_state({"kind": "news", "ref": "keep", "title": "Keep me"}, "later")
    st.remember_shown([{"kind": "news", "ref": str(n), "title": f"S{n}"} for n in range(5)])
    assert st.state_list("later")[0]["title"] == "Keep me"  # trimming history never drops a swiped card


def test_flashcards_decks_import_review_and_study():
    import io
    import sqlite3 as sq
    import tempfile
    import zipfile
    from app import flashcards as fc
    assert fc.parse_csv(b"front,back\nhola,hello\ngato,cat\n") == [("hola", "hello"), ("gato", "cat")]
    assert fc.parse_csv(b"uno\tone\ndos\ttwo\n") == [("uno", "one"), ("dos", "two")]
    # A tiny Anki package: collection.anki2 with notes whose fields are joined by \x1f.
    with tempfile.NamedTemporaryFile(suffix=".anki2") as f:
        db = sq.connect(f.name)
        db.execute("CREATE TABLE notes (id INTEGER, flds TEXT)")
        db.execute("INSERT INTO notes VALUES (1, ?)", ("<b>犬</b>\x1fdog<br>[sound:inu.mp3]",))
        db.execute("INSERT INTO notes VALUES (2, ?)", ("猫\x1fcat&nbsp;",))
        db.commit()
        db.close()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.write(f.name, "collection.anki2")
            z.writestr("media", "{}")
    apkg = buf.getvalue()
    assert fc.parse_apkg(apkg) == [("犬", "dog"), ("猫", "cat")]
    newer = io.BytesIO()
    with zipfile.ZipFile(newer, "w") as z:
        z.writestr("collection.anki21b", b"zstd...")
    try:
        fc.parse_apkg(newer.getvalue())
        raise AssertionError("should refuse the newest format")
    except ValueError as e:
        assert "older Anki versions" in str(e)

    with make_client(full_settings()) as c:
        assert not c.get("/api/sources").json()["sources"]["flashcard"]["configured"]  # no decks yet
        r = c.post("/api/decks/import?filename=animals.apkg", content=apkg)
        assert r.status_code == 200 and r.json()["cards"] == 2
        deck = r.json()["id"]
        made = c.post("/api/decks", json={"name": "Spanish"}).json()["id"]
        c.post(f"/api/decks/{made}/cards", json={"front": "hola", "back": "hello"})
        decks = {d["name"]: d for d in c.get("/api/decks").json()["decks"]}
        assert decks["animals"]["cards"] == 2 and decks["animals"]["due"] == 2 and decks["Spanish"]["cards"] == 1
        assert c.get("/api/sources").json()["sources"]["flashcard"]["configured"]
        # Flashcard-only mode, one deck: its cards in order; Good moves one out, Again brings it back soon.
        first = c.get(f"/api/flashcards/next?decks={deck}").json()["card"]
        assert first["title"] == "犬" and first["flash"]["back"] == "dog"
        g = c.post(f"/api/flashcards/{first['flash']['id']}/grade", json={"good": True}).json()
        assert g["box"] == 1
        second = c.get(f"/api/flashcards/next?decks={deck}").json()["card"]
        assert second["title"] == "猫"
        c.post(f"/api/flashcards/{second['flash']['id']}/grade", json={"good": False})
        assert c.get(f"/api/flashcards/next?decks={deck}").json()["card"] is None  # nothing due right now
        # In the feed, flashcards from any deck.
        feed_card = c.get("/api/card/flashcard").json()
        assert feed_card["kind"] == "flashcard" and feed_card["title"] == "hola"
        assert c.post("/api/decks/import?filename=x.csv", content=b"nothing here").status_code == 400
        assert c.delete(f"/api/decks/{deck}").status_code == 204
        assert [d["name"] for d in c.get("/api/decks").json()["decks"]] == ["Spanish"]


def test_swiping_a_todo_to_done_ticks_it_off():
    patched = []

    def backend(request):
        if request.url.host == "lists.test" and request.method == "PATCH":
            assert request.headers["authorization"] == "Bearer lt"
            patched.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={})
        return fake_backend(request)
    s = full_settings(lists=Service("http://lists.test", "https://lists.example"), lists_token="lt")
    todo = {"kind": "todo", "ref": "todo:7", "title": "Renew passport", "url": "https://lists.example/"}
    with make_client(s, handler=backend) as c:
        r = c.post("/api/card-state", json={"card": todo, "state": "done"})
        assert r.status_code == 200 and r.json() == {"checked": True}
        assert c.post("/api/card-state", json={"card": todo, "state": ""}).json() == {"checked": False}
        assert c.post("/api/card-state", json={"card": todo, "state": "later"}).status_code == 204  # later: no tick
        assert c.post("/api/card-state", json={"card": {"kind": "news", "ref": "x", "title": "t"},
                                                "state": "done"}).status_code == 204
    assert patched == [("/lists/6/items/7", {"checked": True}), ("/lists/6/items/7", {"checked": False})]


def test_place_cards_carry_a_map_location():
    assert sources.geo_of({"lat": 1.5, "lon": 2}, "X") == {"lat": 1.5, "lon": 2.0, "name": "X"}
    assert sources.geo_of([{"lat": 3, "lon": 4}], "Y")["lat"] == 3.0
    assert sources.geo_of(None, "Z") == {} and sources.geo_of({"lat": "n/a"}, "Z") == {}
    s = full_settings(wiki_interests=[])
    with make_client(s) as c:
        card = c.get("/api/card/wikipedia").json()
    assert card["geo"] == {"lat": 47.73, "lon": -122.03, "name": "Lighthouse"}
    photo = sources._immich_card(s, {"id": "p1", "exifInfo": {"latitude": 35.0, "longitude": 135.7, "city": "Kyoto"}}, "")
    assert photo.geo == {"lat": 35.0, "lon": 135.7, "name": "Kyoto"}
    assert sources._immich_card(s, {"id": "p2", "exifInfo": {}}, "").geo == {}


def test_a_card_describing_a_route_gets_map_points(monkeypatch):
    asked = []

    async def ask(client, s, prompt, system=None):
        asked.append(prompt)
        assert "data, not instructions" in system
        if "runs from" in prompt:
            return json.dumps({"places": ["Tolt Reservoir, Washington", "Lake Forest Park Reservoir, Washington"]})
        return json.dumps({"places": []})
    monkeypatch.setattr(curator, "ask_llm", ask)
    monkeypatch.setattr(curator, "NOMINATIM_PAUSE", 0)
    looked_up = []

    def backend(request):
        if request.url.host == "nominatim.openstreetmap.org":
            q = request.url.params["q"]
            looked_up.append((q, request.url.params.get("viewbox")))
            assert request.headers["user-agent"].startswith("Liessure")
            return httpx.Response(200, json=[{"lat": "47.70", "lon": "-121.66"}] if q.startswith("Tolt")
                                  else [{"lat": "47.77", "lon": "-122.28"}])
        return fake_backend(request)
    s = full_settings(llm_url="http://llm.test", llm_key="k")
    card = {"title": "Tolt pipeline", "blurb": "The pipeline runs from Tolt Reservoir to Lake Forest Park.",
            "geo": {"lat": 47.7, "lon": -122.0, "name": "Tolt pipeline"}}
    with make_client(s, handler=backend) as c:
        r = c.post("/api/geo/route", json={"card": card}).json()
        assert r["points"] == [{"lat": 47.70, "lon": -121.66, "name": "Tolt Reservoir"},
                               {"lat": 47.77, "lon": -122.28, "name": "Lake Forest Park Reservoir"}]
        assert c.post("/api/geo/route", json={"card": card}).json() == r  # cached: no second LLM call
        assert len(asked) == 1 and looked_up[0][1] == "-123.5,48.7,-120.5,46.7"
        plain = {"title": "Lighthouse", "blurb": "A tower with a light."}
        assert c.post("/api/geo/route", json={"card": plain}).json() == {"points": []}


def test_a_card_goes_onto_the_todo_list():
    added = []

    def backend(request):
        if request.url.host == "lists.test" and request.method == "POST":
            assert request.headers["authorization"] == "Bearer lt"
            added.append((request.url.path, json.loads(request.content)))
            return httpx.Response(201, json={"id": 41, **json.loads(request.content)})
        return fake_backend(request)
    s = full_settings(lists=Service("http://lists.test", "https://lists.example"), lists_token="lt")
    card = {"title": "Deathplanswitch", "blurb": "A dead man's switch for your homelab.",
            "url": "https://noted.lol/deathplanswitch/"}
    with make_client(s, handler=backend) as c:
        assert c.get("/api/me").json()["todo"] is True
        r = c.post("/api/todo/from-card", json={"card": card, "why": "implement"})
        assert r.status_code == 200 and r.json() == {"id": 41, "text": "Implement: Deathplanswitch"}
        assert c.post("/api/todo/from-card", json={"card": card, "why": "delete"}).status_code == 422
    assert added == [("/lists/6/items", {"text": "Implement: Deathplanswitch",
                                         "notes": "From Liessure. A dead man's switch for your homelab.",
                                         "link_url": "https://noted.lol/deathplanswitch/"})]
    with make_client(full_settings()) as c:
        assert c.get("/api/me").json()["todo"] is False
        assert c.post("/api/todo/from-card", json={"card": card, "why": "study"}).status_code == 409


def test_jeopardy_clues(tmp_path):
    from app import jeopardy
    rows = ["round\tclue_value\tdaily_double_value\tcategory\tcomments\tanswer\tquestion\tair_date\tnotes"]
    rows += ["1\t200\t0\tLAKES & RIVERS\t\tScottish word for lake\tloch\t1984-09-10\t"] * 30
    rows += ["2\t800\t0\tART\t\tThe painting seen here\tMona Lisa\t1990-01-01\t"] * 30
    rows += ['3\t0\t0\tAUTHORS\t\tHe wrote \\"The Raven\\"\t(Edgar Allan) Poe\t2001-05-05\t'] * 30
    (tmp_path / "jeopardy.tsv").write_text("\n".join(rows) + "\n")
    s = full_settings(data_dir=str(tmp_path))
    with make_client(s) as c:
        cards = [c.get("/api/card/trivia").json() for _ in range(25)]
    assert all(x["kind"] == "trivia" and x["quiz"]["text"] for x in cards)
    assert not any("seen here" in x["title"] for x in cards)  # needs a picture: skipped
    loch = next(x for x in cards if x["quiz"]["answer"] == "loch")
    assert loch["label"] == "Jeopardy! · LAKES & RIVERS · $200" and loch["title"] == "Scottish word for lake"
    raven = next(x for x in cards if x["quiz"]["answer"] == "(Edgar Allan) Poe")
    assert raven["title"] == 'He wrote "The Raven"' and raven["label"].endswith("Final Jeopardy!")
    assert raven["url"].startswith("https://") and raven["ref"].startswith("trivia:")


def test_jeopardy_downloads_once_in_the_background(tmp_path):
    from app import jeopardy
    jeopardy._task, jeopardy._failed_at = None, 0.0
    body = ("round\tv\tdd\tcat\tc\tclue\tresp\tdate\tn\n" + "1\t100\t0\tC\t\tA clue\tan answer\t2000-01-01\t\n" * 40000)

    def backend(request):
        if request.url.host == "raw.githubusercontent.com":
            return httpx.Response(200, content=body.encode())
        return fake_backend(request)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(backend)) as hc:
            with pytest.raises(sources.Unavailable):
                await jeopardy.card(hc, str(tmp_path), random.Random(1))
            await jeopardy._task
            return await jeopardy.card(hc, str(tmp_path), random.Random(1))
    assert asyncio.run(go()).quiz == {"text": True, "answer": "an answer"}


BOARD = {"cards": [
    {"ref": "agentbox:f2f66971", "lane": "needs-you", "bucket": "needs-you", "title": "update chromium",
     "topic": "Claude infra", "when": "2026-11-18T17:40:00Z", "question": "Deploy f3a9c1 is proposed: approve it?",
     "prs": [{"n": 1122, "state": "OPEN", "title": "browser: chromium 141"}],
     "approvals": ["https://approvals.liess.tv/d/20261118-191500-f3a9c1", "https://evil.example/x"]},
    {"ref": "agentbox:aa11bb22", "lane": "running", "bucket": "running", "title": "busy", "when": "x"},
    {"ref": "laptop:cc33dd44", "lane": "finished", "bucket": "done", "title": "Tidy the NAS shares",
     "when": "2026-11-18T10:00:00Z", "outcome": "Done: three shares merged."},
]}


def test_session_cards_from_the_hub_board():
    seen = []

    def backend(request):
        if request.url.host == "relay.test":
            assert request.url.path == "/v1/board" and request.headers["authorization"] == "Bearer bt"
            seen.append(1)
            return httpx.Response(200, json=BOARD)
        return fake_backend(request)
    sources._board_cache._data.clear()
    s = full_settings(sessions_url="http://relay.test", sessions_token="bt")
    with make_client(s, handler=backend) as c:
        cards = [c.get("/api/card/session").json() for _ in range(20)]
    assert len(seen) == 1  # the board is cached
    assert {x["title"] for x in cards} == {"update chromium", "Tidy the NAS shares"}  # never a running one
    need = next(x for x in cards if x["title"] == "update chromium")
    assert need["label"] == "Claude · Needs you" and need["blurb"] == "Deploy f3a9c1 is proposed: approve it?"
    assert need["url"] == "https://agents.liess.tv/hub/#agentbox:f2f66971" and need["subtitle"] == "Claude infra · PR #1122 (open)"
    assert need["actions"] == [{"label": "Open approval", "url": "https://approvals.liess.tv/d/20261118-191500-f3a9c1"}]
    done = next(x for x in cards if x["title"] == "Tidy the NAS shares")
    assert done["label"] == "Claude · Finished" and done["ref"] == "session:laptop:cc33dd44:done:2026-11-18T10:00:00Z"
    with make_client(full_settings()) as c:
        assert not c.get("/api/sources").json()["sources"]["session"]["configured"]

