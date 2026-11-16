# Deploy brief: Liessure (for the agent box)

Written 2026-11-16 by the cloud session that built Liessure, which can't
reach wtr. You can. Follow bb's skills for every step; this file only says
what Liessure needs and in what order. Ask axl with `ask-axl` when a
decision is his; every host change goes through a deploy-worker plan he
approves (deploy-approval skill: CHANGES block, nothing shipped-and-run,
`assess.py check` clear before you send the link, `execute <id> when
approved` typed in your session first).

## What it is

`vfd000/liessure`: one container (FastAPI + SQLite on a named volume
`liessure-data`), `docker compose up -d --build` from `/opt/liessure`,
listens on 8000, `/healthz`. About 100-200 MB RAM. It calls out to
Jellyfin, Immich, RomM, Invidious, Wikipedia, news/Nebula RSS, the LiteLLM
gateway, the travel service and the household graph. Nothing calls it but
NPM. Read its README first: Sources, Profile, Curation, Deploy.

The Android app (built by GitHub Actions, installed with Obtainium) loads
`https://liessure.liess.tv/` and answers the gatekeeper's mTLS challenge
with the phone's client cert, so the host must work **off the home
network**: gatekeeper-dynamic list (4) plus the mTLS door, like
activity.liess.tv and watch.liess.tv.

## Order

1. **Household graph first** (vfd000/household-graph, branch
   `proposal/interests`, 44 tests green on SQLite and Postgres 16). Merging
   to `main` deploys it on CT 106 through its runner. Ask axl before
   merging (open the PR, link it). After the runner's deploy:
   - run the seed through a plan (it's a host change on ct 106):
     `docker compose exec -T app python -m app.seed_profile seeds/axl.yaml --as-sub <axl's sub> --dry-run`,
     show axl the counts, then the same without `--dry-run`. axl's sub is
     in the graph's own crosswalk (`whoami` through Concierge) or
     `ZITADEL_USER_SUBS`; don't print secrets.
   - prove it: Concierge → `household_graph_oauth.profile("Axl")`.
2. **Guest** on wtr, built like romm (CT 160) and hgss (CT 159): copy the
   shape of `scripts/romm/deploy/build-plan.sh` into a new
   `scripts/liessure/` in bb (branch + PR). Debian 13, 2 cores, 1024 MB,
   ~8 GB, Docker, server VLAN (tag 2), `firewall=1`, DHCP reservation.
   Re-check the free CT id minutes before proposing (parallel sessions).
   Per-CT firewall: 8000 from NPM only.
3. **Runner** (runner-on-the-box skill): non-root `runner` user in the
   `docker` group, systemd service, repo-level for `vfd000/liessure`.
   `deploy.yml` targets `runs-on: [self-hosted, liessure-r7]`; if the
   house name for runners on wtr guests has moved on from `-r7`, change the
   label in the liessure repo to match rather than misnaming the runner.
   `/opt/liessure` owned by `runner`. The registration token is a secret:
   axl stages it in the CT, per the skill.
4. **Edge** (npm-service-onboarding + npm-tls-cert): NPM host
   `liessure.liess.tv` → CT:8000, its own DNS-01 cert, access list 4 +
   gatekeeper mTLS. Verify from outside (aws-external-scan's pdc vantage is
   free): `403 x-gatekeeper-reason: no-client-cert` on `/`, `/api/feed`,
   `/img`, `/stream/audio/x`.
5. **Reachability from the new CT** before setting URLs. Each must work
   server-side, from the CT's address:
   - Jellyfin: `jellyfin.liess.tv` (or its LAN address).
   - Immich: `photos.liess.tv`. Access list 4 should admit the LAN; the
     `/api/` paths need `x-api-key`, not SSO.
   - RomM: `games.liess.tv` (list 3, internal). The API takes a bearer
     token or basic auth. The axl/Dani accounts are SSO-created; a local
     password or a RomM client token may need minting. Ask axl which.
   - Invidious: `watch.liess.tv` has an SSO shim (`docker/invidious/sso-shim.conf`)
     and `161.fw` only admits NPM on 3000. Find a path for `/api/v1/*`
     and `/vi/*` that skips the shim from this CT (a shim exception or a
     firewall allowance). Don't open it to the world.
   - LiteLLM: `https://llm.liess.tv` (LAN-only).
   - Travel: `https://travel.liess.tv` (list 3).
   - Graph: `https://graph.liess.tv` (list 3).
   - Public: `*.wikipedia.org`, `rss.nebula.app`, the news feeds.
6. **Credentials** (GitHub Actions on vfd000/liessure; secrets vs variables
   as `deploy.yml` reads them). Mint scoped, never reuse an admin key:
   - `JELLYFIN_TOKEN` (a Jellyfin API key) + `JELLYFIN_USER_ID` (axl's
     Jellyfin user id, so "unwatched first" works). Variables:
     `JELLYFIN_URL`, `JELLYFIN_PUBLIC_URL=https://jellyfin.liess.tv`.
   - `IMMICH_API_KEY`, read-only: asset.read, asset.view, album.read,
     memory.read, timeline.read, map.search (search/random and
     search/metadata need asset.read). Variables: `IMMICH_URL`,
     `IMMICH_PUBLIC_URL=https://photos.liess.tv`.
   - `ROMM_TOKEN`, or `ROMM_USERNAME` (variable) + `ROMM_PASSWORD`
     (secret), read-only if RomM allows it. Variables: `ROMM_URL`,
     `ROMM_PUBLIC_URL=https://games.liess.tv`.
   - Invidious variables: `INVIDIOUS_URL` (the server-side path from step
     5), `INVIDIOUS_PUBLIC_URL=https://watch.liess.tv`.
   - `LLM_KEY`: a LiteLLM virtual key scoped to `fast-haiku` only. It
     sends images (photo captions), so check the alias accepts
     `image_url` parts. Variable `LLM_URL=https://llm.liess.tv`.
   - `TRAVEL_TOKEN` (the travel API bearer token) + `TRAVEL_URL`. Trip
     parsing is best-effort: the travel code isn't in its repo, so check
     `/api/trips`' real shape against `app/context.py:parse_trips` and fix
     the parser in a liessure PR if it misses names, dates or places.
   - `GRAPH_TOKEN` (the graph's `MCP_API_TOKEN` bot token) +
     `GRAPH_URL=https://graph.liess.tv`. Leave `GRAPH_PERSON` at `Axl`.
   - `LANGUAGE_PRACTICE_URL=https://practice.liess.tv` only if that's
     live (scripts/language-practice says it was staged, not deployed).
   - Leave the rest unset: the defaults are axl's (Seattle, Telugu then
     Mandarin, Spanish fluent, YouTube links while Videre is broken).
7. **First deploy**: re-run `deploy` on vfd000/liessure (workflow_dispatch;
   two queued runs from earlier pushes can be cancelled). Then prove it:
   - `curl -s http://<ct>:8000/api/sources`: every kind `configured: true`,
     `"profile": "graph"`, `curator.enabled: true`.
   - `curl -s -XPOST http://<ct>:8000/api/curate`: per-kind `picked`
     counts, no `error` keys. Read the container log for failures.
   - `curl -s 'http://<ct>:8000/api/feed?n=10'`: real cards, some with `why`.
8. **Onboarding remainder**: Grafana Alloy log shipping (docker mode),
   service-config-backup row for the `liessure-data` volume (it holds the
   curated pool and open history), hosts-inventory and services-inventory
   rows, `secret-scan.yml` is already in the repo.
9. **Android**: from a machine with keytool and an authenticated `gh`,
   run `scripts/make-keystore.sh vfd000/liessure` from the liessure
   checkout. It sets the four `LIESSURE_KEY*` secrets and prints a
   password: tell axl to save the `.jks` and the password in 1Password, then
   delete the file. Re-run the `android` workflow; it publishes release
   `v0.1.<run>`. Send axl the repo URL for Obtainium (it needs a
   fine-grained read-only token, since the repo is private).

## Done means

axl opens Liessure on his phone off-network and gets curated cards from
every source. Then he can set it as the handler for Reddit links (README,
Android).

Send him one `notify-axl` at the end with the URL, and list anything left
undone and why.
