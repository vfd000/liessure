# Liessure

When the thumb reaches for Reddit, land here instead: an endless feed of
things that are actually yours or actually worth it. A Wikipedia article
about somewhere you've been or are about to go, a game from RomM you can
launch straight into Argosy, a video from YouTube or Nebula, a movie,
series or song on Jellyfin (songs can open Symfonium), a trip photo with a
caption tying it to what's nearby, a story from the Seattle Times, or a
couple of minutes of Telugu, Mandarin, or "hello" in Tagalog.

Two front ends, one server:

- **Web app / PWA** (`app/`): FastAPI serves the feed API and a small static
  page with infinite scroll and per-kind filter chips.
- **Android app** (`android/`): a WebView shell that adds what a browser tab
  can't: it can be the handler for `reddit.com` links, opens cards in the app
  that owns them (YouTube, Immich, Nebula, Seattle Times), launches Symfonium
  for songs and Argosy for games, and answers the gatekeeper's mTLS challenge
  with the client certificate installed on the phone.

## Sources

Every source is optional. One without its URL/credential is left out of the
feed, so a fresh deploy shows Wikipedia and language cards only.

| Kind | Service | Config |
|---|---|---|
| `wikipedia` | Wikipedia random article (best of 3 draws, skips stubs) | `WIKIPEDIA_LANG` (default `en`) |
| `game` | RomM random game. On Android, "Play in Argosy" fires `argosy://launch?romm_id=N` (Argosy downloads it if needed and starts the emulator); "Play in browser" opens EmulatorJS | `ROMM_URL`, `ROMM_TOKEN` *or* `ROMM_USERNAME` + `ROMM_PASSWORD`, `ARGOSY` |
| `video` | Invidious search over a topic list (no Shorts, nothing under 3 min), plus Nebula's RSS feeds | `INVIDIOUS_URL`, `INVIDIOUS_TOPICS`, `NEBULA_FEEDS`, `VIDEO_LINK` |
| `movie`, `show` | Jellyfin random item, unwatched first if a user id is set; trailer plays inline | `JELLYFIN_URL`, `JELLYFIN_TOKEN`, `JELLYFIN_USER_ID` |
| `song` | Jellyfin random track, plays inline; "Open Symfonium" on Android | same, plus `MUSIC_APP_PACKAGE` / `MUSIC_APP_NAME` |
| `photo` | Immich `search/random`, or a today's memory about a third of the time | `IMMICH_URL`, `IMMICH_API_KEY` (read scopes only) |
| `news` | RSS/Atom: Seattle Times, NPR, BBC World, Guardian Science, Ars Technica, HN best by default; last 72 h | `NEWS_FEEDS` (`a|b|c`), `NEWS_MAX_AGE_HOURS` |
| `language` | For a language you're learning (the first twice as often): practice drill, its Wikipedia, learner videos, Lingopie, dictionaries and pronunciation | `LANGUAGES_LEARNING`, `LANGUAGE_PRACTICE_URL`, `LINGOPIE_URL` |
| `homelab` | The tech in your lab (Wikipedia, from a list), home-lab build and design write-ups (ServeTheHome, selfh.st, Jeff Geerling, noted.lol, Lawrence Systems, Home Assistant blog; last 30 days) and videos | `HOMELAB_STACK`, `HOMELAB_FEEDS`, `HOMELAB_TOPICS`, `HOMELAB_MAX_AGE_DAYS` |
| `bite` | Bite-sized takeaways ("why the North Cascades look different") from Wikipedia articles on your interests: each curator pass picks a couple of interests, finds an article, and the LLM writes 3-4 short cards using only facts in that article's text, each linking back to it. Counts for recall | the LLM, your interests (graph or `WIKI_INTERESTS`) |
| `infosec` | Security reading: each article of the latest Phrack issue as its own card (from its table of contents; Phrack has no feed), and write-ups from Project Zero, PortSwigger research, Trail of Bits, ZDI, Krebs, Schneier, tl;dr sec and LWN (last 30 days). Counts for recall | `PHRACK` (on), `INFOSEC_FEEDS`, `INFOSEC_MAX_AGE_DAYS` |
| `job` | Jobs service: a new top match, or an application with a step due within two days. Weekends: due steps only | `JOBS_URL`, `JOBS_TOKEN` (its `MCP_API_TOKEN`), `JOBS_MIN_SCORE` (70) |
| `skill` | What your top job matches keep asking for that the resume doesn't show (the scorer's gaps), most asked first, with a lesson to watch in place | same as `job` |
| `telemetry` | "Lab health": a firing or pending Grafana alert, else a trend (fullest disk, oldest backup, next cert expiry, warmest drive, LLM spend) | `GRAFANA_URL`, `GRAFANA_TOKEN` (Viewer service account), `GRAFANA_PROM_UID` |
| `home` | Home Assistant: solar today (or yesterday, before sunrise), this week, this month and this year, in kWh and dollars, from HA's long-term statistics; the weather | `HA_URL`, `HA_TOKEN`, `HOME_ENERGY_RATE` ($/kWh, 0.22: PSE's effective rate) |
| `math` | A problem or puzzle to solve in the card: mental arithmetic, percentages, fractions, algebra, number theory, counting and probability, sequences, geometry, classic puzzles. Generated on the server (`app/mathgen.py`), so answers are exact; the page checks them (fractions fine), with a hint and a worked solution | none |
| `todo` | Any card's ＋ To do button (owner only) adds "Study: …" or "Implement: …" to the To Do list, linked back to the card. Swiping a to-do card to Done ticks the item off the To Do list (putting it back from the Done list unticks it): Liessure's only write to the lists service. An open item from the household To Do list with an LLM estimate of how long it takes and why; short ones by day, longer ones in the evening. Estimates are kept and redone only when an item changes; the card links, ticking off stays in the lists app | `LISTS_URL`, `LISTS_TOKEN` (the lists service's `MCP_API_TOKEN`), `TODO_LIST_ID` (6), the LLM |
| `math` (more) | A scratch pad to draw your working with a finger; **Tutor me** opens a new Claude chat that coaches the problem step by step without giving it away; every answer is kept, and the card shows today, this week, the daily streak and the weakest topic (`GET /api/math/stats`) | none |
| `recall` | Did it stick? A multiple-choice question about a card you opened a day or more ago, spaced out (1, 3, 7, 21, 60, 180 days; a miss starts it over) | the curator's LLM writes the questions |
| `session` | Owner only. Claude sessions from Session Hub that need you (their question) or finished (their outcome), read from the agent box's fleet relay (`SESSIONS_URL`, `GET /v1/board`, with `SESSIONS_TOKEN`, a token that opens only that route). A card links to the session on the hub board (and to a waiting approval); it never answers or approves anything. Session text is never sent to the curator | — |
| `trivia` | A Jeopardy! clue from jwolle1/jeopardy_clue_dataset (~554k clues, 1984 on), downloaded to the data volume on first use and not kept in this repo. Type your response: "What is", articles, punctuation and small typos are forgiven | — |

**Recall.** When you open or play a Wikipedia, news, home-lab, language or
video card, its text is kept. Each curator pass turns cards opened six or
more hours ago into one question each, from that card's text only. A
question is due a day after you saw the card; answering right moves it to
the next interval, wrong brings it back tomorrow. The card shows how many
you've got right this month.

`job`, `skill`, `telemetry`, `home` and `recall` appear at most once per feed load, and
their titles never go to the curator or the weekly interest proposals (job
text is scraped from the web; the others say nothing about your taste).

**Videos:** "Open" goes to the YouTube app by default (`VIDEO_LINK=youtube`),
with an Invidious button beside it; `VIDEO_LINK=invidious` swaps them once
Videre works again. The inline player always uses the Invidious embed, so
watching in the feed stays private either way. Nebula videos open
nebula.tv, which the Nebula app claims.

**Languages** are `Name:wikipedia-code` lists:
`LANGUAGES_LEARNING=Telugu:te|Mandarin:zh` (first is current),
`LANGUAGES_CURIOUS=French:fr|Korean:ko|Cantonese:yue|Japanese:ja|Russian:ru|Ukrainian:uk|Hindi:hi|Indonesian:id|Tagalog:tl`
(phrase cards only), `LANGUAGES_FLUENT=Spanish:es` (its Wikipedia counts as
ordinary reading, and Spanish documentaries are in the default topics).

**Lingopie** has no public API: `LINGOPIE_URL` is a deep link the official
app opens. A separate client that lists what's actually available is a
follow-up.

Each service also takes `*_PUBLIC_URL`: `*_URL` is where the server calls
the API (can be a LAN address); `*_PUBLIC_URL` is what goes into the links
you tap. Mix is tuned with `LIESSURE_WEIGHTS`, e.g.
`language=5,photo=3,wikipedia=2,game=0`.

Cards are meant to be consumed in place, like a feed: Wikipedia text
expands on tap, photos are full width, songs play inline (`/stream/audio`
proxies Jellyfin with Range support), videos and movie trailers play in an
inline Invidious embed. "Open" goes to the real thing.

Credentials stay server-side. Images from household services go through
`/img`, which only fetches from the configured service bases.

**Offline.** The feed is fetched fresh whenever the network answers. The
page keeps the last 60 cards it was sent (localStorage); when a fetch fails
it shows the ones not already on screen under an "offline" note, and
refreshes once the connection is back. The service worker keeps the page
itself and the last 120 household images; the Android app falls back to its
WebView cache before the service worker is installed. Inline players and
links still need the network.

**Ideas.** 💡 in the header jots down an improvement (with the card you last
opened as context, if you like). They're kept in Liessure's database:
`GET /api/ideas` lists them for a later session to build from.

## Profile (household graph)

Who the feed is for comes from the household graph
([vfd000/household-graph](https://github.com/vfd000/household-graph)):
`GET {GRAPH_URL}/api/profile/{GRAPH_PERSON}` with `GRAPH_TOKEN` (the graph's
bot token; interests are household-visible). It supplies:

- the curator's description of you: loves, likes, curious-abouts, languages,
  active projects, and an explicit "leave out" list from `avoid` topics;
- Wikipedia interest searches (love/like subjects and activities, plus the
  topics of active projects);
- languages: being learned (by priority), curious (phrase cards), fluent;
- home coordinates for the "near home" geosearch.

It's refreshed hourly and before every curation pass. If the graph is unset
or down, the `CURATOR_PROFILE`, `WIKI_INTERESTS`, `LANGUAGES_*` and `HOME_*`
variables are used instead (`/api/sources` says which: `"profile": "graph"`
or `"env"`).

Once a week the curator looks at the last two weeks of opens and proposes up
to three **new interests** to the graph (`POST /api/facts`, kind `interest`,
source `liessure`), each with the titles that suggested it and its Wikidata
id. A proposal needs at least two separate opens behind it. Nothing changes
until you approve it in the graph (`pending_facts` / `decide_fact` through
Concierge); approved, it becomes part of the profile.

## Curation (LLM gateway)

With `LLM_URL` and `LLM_KEY` set, a background job runs every
`CURATE_EVERY_HOURS` (default 12; sooner, at most every 30 min, when a pool
runs low). For each kind it gathers candidates, has the gateway
(`LLM_MODEL`, default `fast-haiku`) judge them against `CURATOR_PROFILE`,
your recent opens and per-kind guidance, and keeps the ones scoring at
least `CURATOR_MIN_SCORE` (7) with a one-line "why":

| Kind | Candidates | "More like what you opened" |
|---|---|---|
| `wikipedia` | Geosearch near home (`HOME_COORDS`), near places in your last 60 days of photos (home excluded), and near/about upcoming trips from the travel service; searches for `WIKI_INTERESTS`; a little random and most-read; most-read on Spanish Wikipedia | `morelike:` search |
| `photo` | Geotagged photos, each with the Wikipedia articles within 5 km. The model looks at the thumbnail (`CURATOR_VISION`), picks the article that fits, and writes the caption; the card links to the article | — |
| `video` | `INVIDIOUS_TOPICS` searches, `CURATOR_CHANNELS` uploads, Nebula feeds | YouTube recommended-next |
| `language` | Learner videos per learning language, `LANGUAGE_CHANNELS` | YouTube recommended-next |
| `game` | 60 games from RomM, judged for a session on a phone or handheld | — |
| `song` | 60 random tracks from Jellyfin, judged for the time of day | Jellyfin `Similar` |
| `news` | recent items from `NEWS_FEEDS` | (recent opens inform the prompt) |

Each pass also writes six **phrase cards** for random learning or curious
languages: greetings, thanks, "how much?", "about the language" and so on,
with script, romanization, meaning and a usage note. They say they're
LLM-written, and "Open" checks the phrase on Wiktionary (plus a Forvo
button to hear it).

Wikipedia candidates are labelled with why they're personal ("near your
photos from 2026-10-02 (Leavenworth)", "upcoming trip: Japan"), and the
model is told to prefer those. Upcoming trips come from `TRAVEL_URL`'s
`/api/trips` with `TRAVEL_TOKEN`; the trip format isn't documented, so the
parser takes names, `start*`/`end*` dates, place names and coordinates from
whatever fields look like them.

"More like" candidates get first claim on the 80-candidate budget.
`CURATOR_SHARE` (0.8) of each kind's cards come from its pool and show the
why; the rest are uncurated so there's still surprise. A pick retires after
3 showings (news after 2 days). Rejected candidates are remembered for 90
days so they aren't re-scored.

Candidate text is untrusted. The model can only choose among the ids (and,
for photos, article titles) it was sent; anything else it returns is ignored.

Opens and plays are recorded via `POST /api/event`, which seeds the "more
like" column. `POST /api/curate[?kind=]` runs a pass now. State is SQLite on
the `liessure-data` volume.

## Sign-in (Zitadel)

Off until `ZITADEL_CLIENT_ID`, `ZITADEL_CLIENT_SECRET` and `ZITADEL_USER_SUBS`
are all in `.env` (`deploy/sso-bootstrap.sh` sets them: run it yourself, it
writes to 1Password). Then every page and API call needs a session:
authorization code + PKCE against `sso.liess.tv`, identity from userinfo, an
allowlist of subs checked on every request (fail closed), and a 30-day
httpOnly session cookie whose sha256 is kept in SQLite.

- The owner (`OWNER_SUB`, axl) keeps all existing data (it's keyed `owner`) and
  sees everything. Everyone else gets their own history, recall questions,
  math progress and ideas, and no job, skill, to-do or lab-health cards (those
  read the owner's own services).
- The curated pool is still picked for the owner's profile; others get fresh
  cards until per-user curation (next).
- Off the home network, the first sign-in needs a gatekeeper IP
  authorisation (sso.liess.tv has no mTLS door); the session then lasts 30 days.
- `GET /api/me` says who's signed in; `/auth/logout` signs out.

## API

- `GET /api/feed?n=6&kinds=photo,game` — a batch of cards (no kind twice in a row)
- `GET /api/card/{kind}` — one card
- `GET /api/sources` — which kinds are configured, curator pool size
- `POST /api/event` — `{kind, ref, action: open|play, title}`
- `POST /api/curate[?kind=video]` — run a curation pass now
- `POST /api/recall/{id}` — `{correct}`: next interval; `GET /api/recall/stats`
- `POST /api/ideas` — `{text, about}`; `GET /api/ideas[?status=new]`;
  `POST /api/ideas/{id}/status` — `{status: new|planned|done|dropped}`
- `GET /healthz`

## Run locally

```sh
pip install -r requirements.txt
DATA_DIR=./data uvicorn --factory app.main:create_app --reload
python -m pytest -q
```

## Deploy

Liessure runs as one container in CT 163 on wtr (10.11.2.56:8000, firewall:
NPM only), created by the deploy-worker plan `deploy/ct-plan.sh`.

1. **Build:** every push to `main` that touches the app runs
   `.github/workflows/image.yml` on GitHub, which publishes
   `ghcr.io/vfd000/liessure` (a public package: the image holds code, never
   secrets). The run summary prints the release command.
2. **Deploy on merge:** CT 163 runs `liessure-autodeploy.timer`
   (`deploy/autodeploy/`, installed by `deploy/build-autodeploy-plan.sh`).
   Every 30 seconds it pulls `:latest` (built only from `main`); when the
   digest is new it pins it in `.env`, recreates the container and waits for
   `/healthz`. An unhealthy build is rolled back and not retried; the next
   build goes out as usual. Log: `journalctl -u liessure-autodeploy` and
   `/var/lib/liessure-autodeploy/deployed` in the CT.
3. **Compose changes** (or a specific older build):
   `bash deploy/build-release-plan.sh ghcr.io/vfd000/liessure@sha256:<digest> <commit>`
   builds a deploy-worker plan that installs that commit's
   `docker-compose.yml`, pins `LIESSURE_IMAGE` to the digest in
   `/opt/liessure/.env`, pulls and recreates the container, and rolls back
   if the health check fails. axl approves it on approvals.liess.tv.

Settings and secrets live only in `/opt/liessure/.env` (0600) on the box.
The graph and travel tokens were copied in by the CT plan; the LLM key is
minted into it by its own plan; Jellyfin, Immich and RomM keys are added
there by hand (see below). Nothing is stored in GitHub secrets.

Still to do:

- [ ] NPM host `liessure.liess.tv` -> 10.11.2.56:8000 with its own DNS-01
      cert, access list 4 + the gatekeeper mTLS door; DHCP reservation for
      10.11.2.56
- [ ] LLM gateway key (plan), then Jellyfin / Immich / RomM read keys in .env
- [ ] Alloy log shipping, a service-config-backup row for the
      `liessure-data` volume, inventory rows, and `ansible/wtr/guests/163.yml`
      in bb so wtr's ansible knows the guest

## Android

`android.yml` builds on GitHub-hosted runners (PRs never touch the
self-hosted one). Each signed build on `main` becomes a GitHub release
tagged `v0.1.<run>` with the APK attached, which is what **Obtainium** tracks.

- Once: `scripts/make-keystore.sh vfd000/liessure` creates the signing key and
  sets the four `LIESSURE_KEY*` secrets. Unsigned builds are never released,
  because a changing debug key would make Android refuse the update.
- Obtainium: add the repo URL. The repo is private, so give Obtainium a
  fine-grained GitHub token with read-only Contents on this repo.
- The web app URL is baked in from `android/gradle.properties` (`liessureUrl`).
- **Jellyfin:** its phone app has no deep links (no VIEW intent filter; it
  always opens the server root), so "Open" on a movie, series or song shows
  Jellyfin's own item page inside Liessure. Back returns to the feed.
- **Songs:** "Play in Symfonium" starts that song through Symfonium's API
  (the `app.symfonium.api.MEDIA_START` broadcast, by title, artist and
  album) and brings the app up. Another `MUSIC_APP_PACKAGE` gets Android's
  play-from-search intent.
- **Phrases:** "Hear it" on a language phrase card speaks it with the phone's
  text-to-speech (install the language's voice in Android's TTS settings, e.g.
  Cantonese, Hindi, Telugu); with no voice it opens Google Translate, which reads
  any phrase aloud.
- **Client certificate (away from home):** the app answers the gatekeeper's
  mTLS check from Android's credential store (Firefox keeps its own store,
  which other apps can't read). When the gatekeeper turns the app away for a
  missing certificate, the app shows **Install certificate** (pick the `.p12`;
  Android asks for its password and stores it) and **Choose another
  certificate**. Signing in through `sso.liess.tv` off the home network still
  needs the gatekeeper's IP authorisation once per 30-day session.
- **Catching Reddit links:** Settings → Apps → Liessure → Open by default →
  Add link → tick the reddit hosts. (Reddit's own app, if installed, has to
  be uninstalled or have its link handling turned off.) Then put Liessure where
  the Reddit icon used to be.
