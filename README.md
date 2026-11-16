# Detour

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
you tap. Mix is tuned with `DETOUR_WEIGHTS`, e.g.
`language=5,photo=3,wikipedia=2,game=0`.

Credentials stay server-side. Images from household services go through
`/img`, which only fetches from the configured service bases.

## API

- `GET /api/feed?n=6&kinds=photo,game` — a batch of cards (no kind twice in a row)
- `GET /api/card/{kind}` — one card
- `GET /api/sources` — which kinds are configured
- `GET /healthz`

## Run locally

```sh
pip install -r requirements.txt
uvicorn app.main:app --reload
python -m pytest -q
```

## Deploy

Same pattern as the other service repos (`runner-on-the-box` in `bb`):
merge to `main` → `deploy.yml` on the self-hosted runner `detour-r7` on
Detour's own LXC → `rsync` to `/opt/detour` → `docker compose up -d --build`.
Non-secret settings are GitHub Actions **variables**, credentials are
**secrets** (`JELLYFIN_TOKEN`, `IMMICH_API_KEY`, `ROMM_TOKEN`/`ROMM_PASSWORD`).

Not done yet (needs the LXC):

- [ ] LXC + runner `detour-r7`, `/opt/detour` owned by the runner user
- [ ] NPM host `detour.liess.tv` with its own DNS-01 cert, on the
      gatekeeper list + mTLS door (the app has no login of its own yet)
- [ ] Per-CT firewall: 8000 from NPM only
- [ ] Alloy log shipping, inventory rows
- [ ] A Jellyfin API key, a read-only Immich key, a RomM credential

## Android

`android.yml` builds on GitHub-hosted runners (PRs never touch the
self-hosted one). On `main` it publishes the APK as the **android-latest**
release, so the phone can grab it from the releases page.

- Set `DETOUR_KEYSTORE_B64` / `DETOUR_KEYSTORE_PASSWORD` / `DETOUR_KEY_ALIAS` /
  `DETOUR_KEY_PASSWORD` secrets so builds update in place; without them the
  APK is debug-signed with a key that changes per build.
- The web app URL is baked in from `android/gradle.properties` (`detourUrl`).
- **Catching Reddit links:** Settings → Apps → Detour → Open by default →
  Add link → tick the reddit hosts. (Reddit's own app, if installed, has to
  be uninstalled or have its link handling turned off.) Then put Detour where
  the Reddit icon used to be.
