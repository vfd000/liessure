#!/usr/bin/env bash
# CHANGES
#   ct 163: read-only preflight: hostname is liessure; /opt/liessure/.env has empty JELLYFIN_TOKEN=,
#           JELLYFIN_USER_ID= and IMMICH_API_KEY= lines
#   ct 126: read-only preflight: /root/mcp-server/.env has JELLYFIN_API_KEY and IMMICH_API_KEY lines (existence
#           only); Jellyfin has no API key named Liessure yet (GET /Auth/Keys, names only)
#   ct 126: uses its JELLYFIN_API_KEY (never printed) to create a new Jellyfin API key named Liessure
#           (POST https://jellyfin.liess.tv/Auth/Keys?app=Liessure), reads that key's token back from
#           GET /Auth/Keys into /root/minted-liessure/jellyfin.key (0600, mkdir'd for this run)
#   ct 163: in /opt/liessure/.env sets JELLYFIN_TOKEN (the new key, piped from ct 126 through wtr, never printed),
#           JELLYFIN_USER_ID=95eb5e298d67489ab6fac51436f5bf9f (axl's Jellyfin user, from household-mcp's
#           config.env PLAYBACK_JELLYFIN_USERS; not a secret) and IMMICH_API_KEY (a copy of household-mcp's
#           read-only Immich key, piped the same way: Liessure and household-mcp then share it)
#   ct 126: shreds /root/minted-liessure/jellyfin.key and removes the directory
#   ct 163: checks with the new values (HTTP codes only): GET https://jellyfin.liess.tv/Items?Limit=1 as that user,
#           POST https://photos.liess.tv/api/search/random {size:1}
#   ct 163: recreates the liessure container (docker compose up -d --no-build --force-recreate) so it reads .env,
#           waits for /healthz, then prints the first 1200 bytes of /api/sources (which kinds are on; no secrets)
#   On any failure after the Jellyfin key exists: deletes that key in Jellyfin (DELETE /Auth/Keys/<key>), shreds
#   the minted file, and puts the three .env lines back to empty.
# END CHANGES
#
# Liessure's Jellyfin and Immich keys, without anyone typing a key.
set -euo pipefail

WTR="axl@10.11.2.233"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
say() { echo "==> $*"; }
wtr() { ssh "${SSH_OPTS[@]}" "$WTR" "$@" </dev/null; }
host_sh() { wtr "sudo bash -c $(printf %q "$1")"; }
mcp() { wtr "sudo pct exec 126 -- bash -c $(printf %q "$1")"; }
app() { wtr "sudo pct exec 163 -- bash -c $(printf %q "$1")"; }
JF_USER=95eb5e298d67489ab6fac51436f5bf9f
MINTED=0

# Runs in ct 126: how many Jellyfin API keys are named Liessure (names only).
read -r -d '' COUNT <<'CMD' || true
k="$(grep -m1 '^JELLYFIN_API_KEY=' /root/mcp-server/.env | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
curl -fsS -m 20 -H "X-Emby-Token: $k" https://jellyfin.liess.tv/Auth/Keys \
  | python3 -c 'import json,sys; print(sum(1 for i in json.load(sys.stdin).get("Items",[]) if i.get("AppName")=="Liessure"))'
CMD

# Runs in ct 126: create the key and keep only its token, in a 0600 file.
read -r -d '' MINT <<'CMD' || true
set -euo pipefail
umask 077
install -d -m 0700 /root/minted-liessure
k="$(grep -m1 '^JELLYFIN_API_KEY=' /root/mcp-server/.env | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
curl -fsS -m 20 -o /dev/null -w 'Auth/Keys create: HTTP %{http_code}\n' -X POST -H "X-Emby-Token: $k" \
  'https://jellyfin.liess.tv/Auth/Keys?app=Liessure'
curl -fsS -m 20 -H "X-Emby-Token: $k" https://jellyfin.liess.tv/Auth/Keys \
  | python3 -c 'import json,sys; ks=[i for i in json.load(sys.stdin).get("Items",[]) if i.get("AppName")=="Liessure"]; assert len(ks)==1, len(ks); print(ks[0]["AccessToken"])' \
  > /root/minted-liessure/jellyfin.key
test -s /root/minted-liessure/jellyfin.key
CMD

# Runs in ct 126: delete every Jellyfin key named Liessure (the rollback).
read -r -d '' REVOKE <<'CMD' || true
k="$(grep -m1 '^JELLYFIN_API_KEY=' /root/mcp-server/.env | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
for t in $(curl -fsS -m 20 -H "X-Emby-Token: $k" https://jellyfin.liess.tv/Auth/Keys \
    | python3 -c 'import json,sys; [print(i["AccessToken"]) for i in json.load(sys.stdin).get("Items",[]) if i.get("AppName")=="Liessure"]'); do
  curl -s -m 20 -o /dev/null -w 'Auth/Keys delete: HTTP %{http_code}\n' -X DELETE -H "X-Emby-Token: $k" "https://jellyfin.liess.tv/Auth/Keys/$t"
done
shred -u /root/minted-liessure/jellyfin.key 2>/dev/null || true
rm -rf /root/minted-liessure
CMD

# Runs in ct 163: prove the new values work (codes only).
read -r -d '' PROBE <<'CMD' || true
j="$(grep -m1 '^JELLYFIN_TOKEN=' /opt/liessure/.env | cut -d= -f2-)"
i="$(grep -m1 '^IMMICH_API_KEY=' /opt/liessure/.env | cut -d= -f2-)"
u="$(grep -m1 '^JELLYFIN_USER_ID=' /opt/liessure/.env | cut -d= -f2-)"
a="$(curl -s -m 20 -o /dev/null -w '%{http_code}' -H "X-Emby-Token: $j" "https://jellyfin.liess.tv/Items?Limit=1&Recursive=true&userId=$u")"
b="$(curl -s -m 20 -o /dev/null -w '%{http_code}' -X POST -H "x-api-key: $i" -H 'Content-Type: application/json' \
  --data-binary '{"size":1}' https://photos.liess.tv/api/search/random)"
echo "$a $b"
CMD

undo() {
  [[ $MINTED == 1 ]] || return 0
  echo "UNDO: deleting the Liessure Jellyfin key and clearing the three .env lines" >&2
  mcp "$REVOKE" || true
  app "sed -i -e '/^JELLYFIN_TOKEN=/d' -e '/^JELLYFIN_USER_ID=/d' -e '/^IMMICH_API_KEY=/d' /opt/liessure/.env && printf '%s\n' 'JELLYFIN_TOKEN=' 'JELLYFIN_USER_ID=' 'IMMICH_API_KEY=' >> /opt/liessure/.env" || true
}
die() { echo "ABORT: $*" >&2; undo; exit 1; }
set -E
trap 'rc=$?; trap - ERR; undo; exit $rc' ERR

say "1. preflight"
[[ "$(wtr "sudo pct exec 163 -- hostname")" == liessure ]] || die "CT 163 is not liessure"
for k in JELLYFIN_TOKEN JELLYFIN_USER_ID IMMICH_API_KEY; do
  app "grep -qx '$k=' /opt/liessure/.env" || die "$k is not an empty line in CT 163's .env (already set?)"
done
mcp "grep -q '^JELLYFIN_API_KEY=.' /root/mcp-server/.env" || die "no JELLYFIN_API_KEY in ct 126"
mcp "grep -q '^IMMICH_API_KEY=.' /root/mcp-server/.env" || die "no IMMICH_API_KEY in ct 126"
n="$(mcp "$COUNT")" || die "could not list Jellyfin API keys"
[[ "$n" == 0 ]] || die "Jellyfin already has $n key(s) named Liessure"

say "2. ct 126: create the Liessure Jellyfin key"
MINTED=1
mcp "$MINT"

say "3. move the values into ct 163's .env"
app "sed -i -e '/^JELLYFIN_TOKEN=/d' -e '/^JELLYFIN_USER_ID=/d' -e '/^IMMICH_API_KEY=/d' /opt/liessure/.env"
host_sh "{ printf 'JELLYFIN_TOKEN='; pct exec 126 -- cat /root/minted-liessure/jellyfin.key | tr -d '\r\n'; echo; } | pct exec 163 -- tee -a /opt/liessure/.env >/dev/null"
app "printf 'JELLYFIN_USER_ID=%s\n' $JF_USER >> /opt/liessure/.env"
host_sh "pct exec 126 -- grep -m1 '^IMMICH_API_KEY=' /root/mcp-server/.env | sed -e 's/=\"/=/' -e 's/\"\$//' | pct exec 163 -- tee -a /opt/liessure/.env >/dev/null"
mcp "shred -u /root/minted-liessure/jellyfin.key && rmdir /root/minted-liessure"
for k in JELLYFIN_TOKEN JELLYFIN_USER_ID IMMICH_API_KEY; do
  app "test \$(grep -c '^$k=.' /opt/liessure/.env) = 1" || die "$k did not land"
done

say "4. check the values from ct 163"
codes="$(app "$PROBE")" || codes=""
say "    jellyfin items: ${codes% *}, immich random: ${codes#* }"
[[ "$codes" == "200 200" ]] || die "a new value does not work (jellyfin, immich: $codes)"

say "5. recreate Liessure so it reads .env"
app "cd /opt/liessure && docker compose up -d --no-build --force-recreate 2>&1 | tail -3"
ok=""
for i in $(seq 1 30); do
  if app "curl -fsS -m 3 http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] || die "Liessure did not come back healthy"
app "curl -fsS -m 10 http://127.0.0.1:8000/api/sources | head -c 1200; echo" || say "    (sources summary unavailable)"
trap - ERR
say "DONE: Jellyfin (own key, Liessure) and Immich (household-mcp's read-only key) are on"
