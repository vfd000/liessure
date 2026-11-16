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
| `news` | RSS/Atom feeds (NPR, BBC World, Guardian Science, Ars Technica, HN best by default), last 72 h | `NEWS_FEEDS` (`a|b|c`), `NEWS_MAX_AGE_HOURS` |
| `language` | Practice drill link, Lingopie, Telugu Wikipedia, Telugu videos via Invidious, curated resources | `LANGUAGE_NAME`, `LANGUAGE_WIKIPEDIA`, `LANGUAGE_PRACTICE_URL`, `LANGUAGE_VIDEO_QUERIES` |

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

| Kind | Broad candidates | "More like what you opened" |
|---|---|---|
| `video` | `INVIDIOUS_TOPICS` searches, `CURATOR_CHANNELS` uploads | YouTube recommended-next |
| `language` | `LANGUAGE_VIDEO_QUERIES`, `LANGUAGE_CHANNELS` (judged as a learner) | YouTube recommended-next |
| `wikipedia` | 20 random + today's 20 most-read | `morelike:` search |
| `song` | 60 random tracks from Jellyfin | Jellyfin `Similar` |
| `news` | recent items from `NEWS_FEEDS` (RSS/Atom) | (recent opens inform the prompt) |

"More like" candidates get first claim on the 80-candidate budget.
`CURATOR_SHARE` (0.8) of each kind's cards come from its pool and show the
why; the rest are uncurated so there's still surprise. A pick retires after
3 showings (news after 2 days). Rejected candidates are remembered for 90
days so they aren't re-scored.

Candidate text is untrusted. The model can only choose among the ids it was
sent; anything else it returns is ignored.

Opens and plays are recorded via `POST /api/event`, which seeds the "more
like" column. `POST /api/curate[?kind=]` runs a pass now. State is SQLite on
the `liessure-data` volume.

Lingopie has no public API, so it isn't curated: set `LINGOPIE_URL` and it
appears as a language card.

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
