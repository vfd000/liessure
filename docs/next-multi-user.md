# Next milestone: multi-user Liessure with connected accounts

Decided 2026-11-16: deploy single-user first (keys in `/opt/liessure/.env`),
then this.

## Goal

Each household member signs in, connects their own accounts without
copying tokens around, and gets a feed built from their own profile in
the household graph (Dani: K-dramas, Disney; axl: everything in
`profile("Axl Liess")`).

## Pieces

1. **Sign-in.** Zitadel OIDC, a new `liessure-web` app in the bb-services
   project, users gated by `ZITADEL_USER_SUBS` like activity-tracker. The
   Android WebView already keeps cookies; the gatekeeper mTLS door stays in
   front.
2. **Per-user profile.** Resolve the signed-in sub to a graph entry
   (`zitadel:<sub>` through `/api/profile`), so each user's curator prompt,
   interests and languages are their own. Pools and events gain a `user`
   column.
3. **Connections page**, one row per service, with the best flow each
   service supports:

   | Service | Flow | User effort |
   |---|---|---|
   | Jellyfin | Quick Connect (`/QuickConnect/Initiate`, the user approves in Jellyfin, Liessure polls `/QuickConnect/Connect` and authenticates with the secret) | one tap in Jellyfin |
   | RomM 5.4.1 | client-token pairing: the user creates a read-scoped token and taps "pair", and Liessure exchanges the code at `/api/client-tokens/exchange` | create the token, then type the code |
   | RomM (better) | an `authorize` page upstream: app redirect -> consent screen listing scopes -> RomM mints the token and redirects back with a pair code -> exchange. RomM already has scopes, client tokens and pair codes; only the consent and redirect step is missing | one tap |
   | Immich | API key only; deep link to the API-keys page plus the exact read scopes, then paste | paste once |
   | Invidious | the server mints the watch-token line per user (deploy plan) | none |

   Read scopes for a RomM token: `roms.read roms.user.read platforms.read
   assets.read collections.read me.read`. With `roms.user.read` the
   curator can favour backlogged and unplayed games.
4. **Storage.** Per-user credentials encrypted at rest in Liessure's
   SQLite (Fernet, key in `.env`), never sent to the browser. If another
   household app wants the same connections, move this into a small shared
   connections service with the same table shape. Not the household graph:
   it holds facts, not secrets.

## Not in scope

Cross-user play history (RomM only returns a user's own; reading it for
everyone needs direct database access, which isn't worth opening up).

## Later: session cards

Cards for Claude sessions that need axl or have finished, so the feed is
also where he catches up on the fleet. Source: the session hub's
`scripts/claude-fleet/fleet.py brief` (bb, on the agent box): each session's
`needs input:` question and what changed since the last brief, plus the
deploy worker's `list_deploys` for anything `proposed` (needs an approval
click) or failed. Card kinds: "needs you" (the question, a tap opens that
session in the agents.liess.tv console), "approval waiting" (opens
approvals.liess.tv/d/<id>), "done" (the session's own summary). Reading
fleet state needs an endpoint on the agent box, or the box pushing a small
JSON to Liessure; never the box's credentials in Liessure. Answers stay in
the console: a card links, it doesn't act.

## Later: to-do cards

Cards for open items on axl's to-do list (the household `lists` service,
lists.liess.tv; `lists_oauth` through Concierge), so a spare minute in the
feed can go to something he actually wants done. Each card: the item, an
LLM estimate of how long it takes ("5 min", "an evening") with the
assumption behind it, and links that get it started (the doc, the store
page, the PR, the household graph entry it mentions). Favour items that fit
the moment: short ones on the phone, longer ones in the evening. Ticking it
off stays in the lists app; the card links, it doesn't act. Estimates are
cached per item and redone only when the item's text changes.
