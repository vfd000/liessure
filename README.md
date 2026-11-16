# Liessure

When the thumb reaches for Reddit, land here instead: an endless feed of
things that are actually yours or actually worth it. A random Wikipedia
article, a game on RomM, a video on Invidious, a movie, series or song on
Jellyfin (songs can open Symfonium), a photo or "on this day" memory from
Immich, or a few minutes of Telugu.

Two front ends, one server:

- **Web app / PWA** (`app/`): FastAPI serves the feed API and a small static
  page with infinite scroll and per-kind filter chips.
- **Android app** (`android/`): a WebView shell that adds what a browser tab
  can't: it can be the handler for `reddit.com` links, opens cards in the app
  that owns them, launches Symfonium for songs, and answers the gatekeeper's
  mTLS challenge with the client certificate installed on the phone.

## Sources

Every source is optional. One without its URL/credential is left out of the
feed, so a fresh deploy shows Wikipedia and language cards only.

| Kind | Service | Config |
|---|---|---|
| `wikipedia` | Wikipedia random article (best of 3 draws, skips stubs) | `WIKIPEDIA_LANG` (default `en`) |
| `game` | RomM `GET /api/roms` at a random offset; "Play in browser" opens `/rom/{id}/ejs` | `ROMM_URL`, `ROMM_TOKEN` *or* `ROMM_USERNAME` + `ROMM_PASSWORD` |
| `video` | Invidious search over a topic list; drops Shorts and anything under 3 min | `INVIDIOUS_URL`, `INVIDIOUS_TOPICS` (`a|b|c`) |
| `movie`, `show` | Jellyfin random item, unwatched first if a user id is set | `JELLYFIN_URL`, `JELLYFIN_TOKEN`, `JELLYFIN_USER_ID` |
| `song` | Jellyfin random track; "Open Symfonium" on Android | same, plus `MUSIC_APP_PACKAGE` / `MUSIC_APP_NAME` |
| `photo` | Immich `search/random`, or a today's memory about a third of the time | `IMMICH_URL`, `IMMICH_API_KEY` (read scopes only) |
| `language` | Practice drill link, Telugu Wikipedia, Telugu videos via Invidious, curated resources | `LANGUAGE_NAME`, `LANGUAGE_WIKIPEDIA`, `LANGUAGE_PRACTICE_URL`, `LANGUAGE_VIDEO_QUERIES` |

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

With `LLM_URL`, `LLM_KEY` and Invidious configured, a background job runs
every `CURATE_EVERY_HOURS` (default 12, sooner when the pool runs low):

1. **Candidates** from Invidious: your `INVIDIOUS_TOPICS` searches, the
   latest uploads from `CURATOR_CHANNELS` (channel ids, `|`-separated), and
   the *recommended next* list of each video you recently opened or played
   here. Shorts, live streams and anything already scored are dropped; up
   to 80 go forward, with the "recommended next" ones first.
2. **Judging** by the household LiteLLM gateway (`LLM_MODEL`, default
   `fast-haiku`) against `CURATOR_PROFILE` and your recent opens. It returns
   a score and a one-line "why" per pick. Picks scoring at least
   `CURATOR_MIN_SCORE` (7) join the pool; the rest are remembered as
   rejected so they aren't scored again.
3. **Serving**: 80% of video cards come from the pool ("Picked for you",
   with the why as the blurb), the rest from raw search so there's still
   surprise. A pick retires after it's been shown 3 times.

Titles from YouTube are untrusted. The model can only choose among the ids
it was sent, and anything else it returns is ignored.

Opens and plays are recorded via `POST /api/event`, which is what feeds the
"recommended next" seeds. `POST /api/curate` runs a pass on demand. State
lives in SQLite on the `liessure-data` volume.

## API

- `GET /api/feed?n=6&kinds=photo,game` — a batch of cards (no kind twice in a row)
- `GET /api/card/{kind}` — one card
- `GET /api/sources` — which kinds are configured, curator pool size
- `POST /api/event` — `{kind, ref, action: open|play, title}`
- `POST /api/curate` — run a curation pass now
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
