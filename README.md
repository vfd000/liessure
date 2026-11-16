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

## API

- `GET /api/feed?n=6&kinds=photo,game` — a batch of cards (no kind twice in a row)
- `GET /api/card/{kind}` — one card
- `GET /api/sources` — which kinds are configured, curator pool size
- `POST /api/event` — `{kind, ref, action: open|play, title}`
- `POST /api/curate[?kind=video]` — run a curation pass now
- `GET /healthz`

## Run locally

```sh
pip install -r requirements.txt
uvicorn app.main:app --reload
python -m pytest -q
```

## Deploy

Same pattern as the other service repos (`runner-on-the-box` in `bb`):
merge to `main` → `deploy.yml` on the self-hosted runner `liessure-r7` on
Liessure's own LXC → `rsync` to `/opt/liessure` → `docker compose up -d --build`.
Non-secret settings are GitHub Actions **variables**, credentials are
**secrets** (`JELLYFIN_TOKEN`, `IMMICH_API_KEY`, `ROMM_TOKEN`/`ROMM_PASSWORD`).

Not done yet (needs the LXC):

- [ ] LXC + runner `liessure-r7`, `/opt/liessure` owned by the runner user
- [ ] NPM host `liessure.liess.tv` with its own DNS-01 cert, on the
      gatekeeper list + mTLS door (the app has no login of its own yet)
- [ ] Per-CT firewall: 8000 from NPM only
- [ ] Alloy log shipping, inventory rows
- [ ] A Jellyfin API key, a read-only Immich key, a RomM credential
- [ ] A LiteLLM virtual key scoped to `fast-haiku` only, as secret `LLM_KEY`;
      `LLM_URL=https://llm.liess.tv` (LAN-only, which the LXC is)
- [ ] A travel API token as secret `TRAVEL_TOKEN`, `TRAVEL_URL=https://travel.liess.tv`
- [ ] Check the Seattle Times feed: subscriber links open in the browser or
      app where you're signed in, but the feed itself is public headlines

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
- **Catching Reddit links:** Settings → Apps → Liessure → Open by default →
  Add link → tick the reddit hosts. (Reddit's own app, if installed, has to
  be uninstalled or have its link handling turned off.) Then put Liessure where
  the Reddit icon used to be.
