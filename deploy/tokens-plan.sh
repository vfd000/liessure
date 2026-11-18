#!/usr/bin/env bash
# CHANGES
#   ct 163: read-only preflight: hostname is liessure; /opt/liessure/.env has no JOBS_*, GRAFANA_* or HA_* lines yet
#   ct 149: read-only: reads MCP_API_TOKEN from the running jobs app container's environment (never printed)
#   ct 110: reads GRAFANA_ADMIN_PASSWORD from /opt/telemetry/.env (never printed); checks Grafana has no service
#           account named liessure; creates one (Viewer role) and a token for it via Grafana's API on localhost:3000,
#           kept in /root/minted-liessure/grafana.key (0600) only until it is copied. A Viewer can query every
#           datasource through Grafana, Loki included (same reach as the existing mcp-grafana account)
#   ct 126: read-only: reads HOME_ASSISTANT_MCP_TOKEN from /root/mcp-server/.env (never printed). Liessure then shares
#           household-mcp's Home Assistant token: revoking that token turns off Liessure's home cards too
#   ct 163: appends to /opt/liessure/.env (0600): JOBS_URL=https://jobs.liess.tv, JOBS_TOKEN (the jobs token),
#           GRAFANA_URL=https://telemetry.liess.tv, GRAFANA_TOKEN (the new Grafana token),
#           HA_URL=https://homeassistant.liess.tv, HA_TOKEN (the Home Assistant token); values piped through wtr
#   ct 110: shreds /root/minted-liessure/grafana.key and removes the directory
#   ct 163: checks each token from inside (HTTP codes only): GET jobs.liess.tv/api/status, GET
#           telemetry.liess.tv/api/prometheus/grafana/api/v1/alerts, GET homeassistant.liess.tv/api/
#   ct 163: replaces /etc/systemd/system/liessure-autodeploy.timer (data, sha256-checked) so the deploy check runs
#           every 30 s instead of every 2 min; daemon-reload; restarts the timer
#   ct 163: recreates the liessure container (docker compose up -d --no-build --force-recreate, holding the autodeploy
#           lock so the timer can't race it), waits for /healthz, prints the first 1200 bytes of /api/sources
#   On any failure after a change: deletes the liessure Grafana service account, shreds the minted file, removes the
#   six .env lines and recreates the container again (the timer file stays: it is harmless either way)
#   wtr: no files or settings change. Every step is an ssh to axl@10.11.2.233 (BatchMode,
#   StrictHostKeyChecking=accept-new: a first-seen key is accepted, a changed one fails) running sudo pct exec. The
#   three copies in step 3 run as sudo bash -c pipelines on wtr: pct exec <source ct> ... | pct exec 163 -- tee -a
#   .env, so each token passes through a pipe in wtr's memory (never written to wtr's disk, never printed)
# END CHANGES
#
# Liessure's jobs, Grafana and Home Assistant tokens, without anyone typing one; and the 30 s deploy timer.
set -euo pipefail

WTR="axl@10.11.2.233"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
say() { echo "==> $*"; }
wtr() { ssh "${SSH_OPTS[@]}" "$WTR" "$@" </dev/null; }
host_sh() { wtr "sudo bash -c $(printf %q "$1")"; }
ct() { local id="$1"; shift; wtr "sudo pct exec $id -- bash -c $(printf %q "$1")"; }
push() { ssh "${SSH_OPTS[@]}" "$WTR" "sudo pct exec $1 -- tee $2" < "$3" >/dev/null; }
sha() { sha256sum < "$1" | cut -d' ' -f1; }
LOCAL_TMP="$(mktemp -d /tmp/liessure-tokens.XXXXXXXX)"; chmod 700 "$LOCAL_TMP"
trap 'rm -rf "$LOCAL_TMP"' EXIT
KEYS="JOBS_URL JOBS_TOKEN GRAFANA_URL GRAFANA_TOKEN HA_URL HA_TOKEN"
CHANGED=0

# Runs in ct 110: the Grafana admin login as a curl config on stdin, so the password never shows in ps.
read -r -d '' GF_AUTH <<'CMD' || true
p="$(grep -m1 '^GRAFANA_ADMIN_PASSWORD=' /opt/telemetry/.env | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
gf() { printf 'user = "admin:%s"\n' "$p" | curl -fsS -m 20 -K - -H 'Content-Type: application/json' "$@"; }
CMD

# Runs in ct 110: how many service accounts are named liessure.
read -r -d '' GF_COUNT <<CMD || true
$GF_AUTH
gf 'http://localhost:3000/api/serviceaccounts/search?query=liessure' \
  | python3 -c 'import json,sys; print(sum(1 for a in json.load(sys.stdin).get("serviceAccounts",[]) if a.get("name")=="liessure"))'
CMD

# Runs in ct 110: create the Viewer service account and keep only its token, in a 0600 file.
read -r -d '' GF_MINT <<CMD || true
set -euo pipefail
umask 077
install -d -m 0700 /root/minted-liessure
$GF_AUTH
id="\$(gf -X POST -d '{"name":"liessure","role":"Viewer","isDisabled":false}' http://localhost:3000/api/serviceaccounts \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
echo "service account liessure: id \$id"
gf -X POST -d '{"name":"liessure"}' "http://localhost:3000/api/serviceaccounts/\$id/tokens" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])' > /root/minted-liessure/grafana.key
test -s /root/minted-liessure/grafana.key
CMD

# Runs in ct 110: delete every service account named liessure (the rollback).
read -r -d '' GF_REVOKE <<CMD || true
$GF_AUTH
for id in \$(gf 'http://localhost:3000/api/serviceaccounts/search?query=liessure' \
    | python3 -c 'import json,sys; [print(a["id"]) for a in json.load(sys.stdin).get("serviceAccounts",[]) if a.get("name")=="liessure"]'); do
  gf -o /dev/null -w 'service account delete: HTTP %{http_code}\n' -X DELETE "http://localhost:3000/api/serviceaccounts/\$id" || true
done
shred -u /root/minted-liessure/grafana.key 2>/dev/null || true
rm -rf /root/minted-liessure
CMD

# Runs in ct 149: the jobs token, from the running app container's environment.
read -r -d '' JOBS_READ <<'CMD' || true
cd /opt/jobs && docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$(docker compose -p jobs ps -q app)" \
  | sed -n 's/^MCP_API_TOKEN=//p' | head -1 | tr -d '\r\n'
CMD

# Runs in ct 163: does each token work? (HTTP codes only)
read -r -d '' PROBE <<'CMD' || true
v() { grep -m1 "^$1=" /opt/liessure/.env | cut -d= -f2-; }
a="$(curl -s -m 20 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $(v JOBS_TOKEN)" "$(v JOBS_URL)/api/status")"
b="$(curl -s -m 20 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $(v GRAFANA_TOKEN)" "$(v GRAFANA_URL)/api/prometheus/grafana/api/v1/alerts")"
c="$(curl -s -m 20 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $(v HA_TOKEN)" "$(v HA_URL)/api/")"
echo "$a $b $c"
CMD

drop_lines() { ct 163 "cd /opt/liessure && sed -i -e '/^JOBS_URL=/d' -e '/^JOBS_TOKEN=/d' -e '/^GRAFANA_URL=/d' -e '/^GRAFANA_TOKEN=/d' -e '/^HA_URL=/d' -e '/^HA_TOKEN=/d' .env"; }
undo() {
  [[ $CHANGED == 1 ]] || return 0
  echo "UNDO: deleting the liessure Grafana service account and taking the six .env lines back out" >&2
  ct 110 "$GF_REVOKE" || true
  drop_lines || true
  ct 163 "cd /opt/liessure && flock /var/lib/liessure-autodeploy/lock docker compose up -d --no-build --force-recreate 2>&1 | tail -3" || true
}
die() { echo "ABORT: $*" >&2; undo; exit 1; }
set -E
trap 'rc=$?; trap - ERR; undo; exit $rc' ERR

say "1. preflight (read-only)"
[[ "$(wtr "sudo pct exec 163 -- hostname")" == liessure ]] || die "CT 163 is not liessure"
for k in $KEYS; do
  ct 163 "! grep -q '^$k=' /opt/liessure/.env" || die "$k is already in CT 163's .env"
done
ct 110 "test -f /opt/telemetry/.env && grep -q '^GRAFANA_ADMIN_PASSWORD=.' /opt/telemetry/.env && command -v python3 >/dev/null" \
  || die "ct 110: no GRAFANA_ADMIN_PASSWORD or no python3"
ct 126 "grep -q '^HOME_ASSISTANT_MCP_TOKEN=.' /root/mcp-server/.env" || die "ct 126: no HOME_ASSISTANT_MCP_TOKEN"
[[ "$(ct 149 "$JOBS_READ" | wc -c)" -gt 16 ]] || die "ct 149: could not read the jobs app's MCP_API_TOKEN"
n="$(ct 110 "$GF_COUNT")" || die "could not list Grafana service accounts"
[[ "$n" == 0 ]] || die "Grafana already has $n service account(s) named liessure"

say "2. ct 110: create the liessure Grafana service account (Viewer) and its token"
CHANGED=1
ct 110 "$GF_MINT"

say "3. copy the values into ct 163's .env"
host_sh "{ printf 'JOBS_TOKEN='; pct exec 149 -- bash -c $(printf %q "$JOBS_READ"); echo; } | pct exec 163 -- tee -a /opt/liessure/.env >/dev/null"
host_sh "{ printf 'GRAFANA_TOKEN='; pct exec 110 -- cat /root/minted-liessure/grafana.key | tr -d '\r\n'; echo; } | pct exec 163 -- tee -a /opt/liessure/.env >/dev/null"
host_sh "pct exec 126 -- grep -m1 '^HOME_ASSISTANT_MCP_TOKEN=' /root/mcp-server/.env | sed -e 's/^HOME_ASSISTANT_MCP_TOKEN=/HA_TOKEN=/' -e 's/=\"/=/' -e 's/\"\$//' | pct exec 163 -- tee -a /opt/liessure/.env >/dev/null"
ct 163 "printf '%s\n' JOBS_URL=https://jobs.liess.tv GRAFANA_URL=https://telemetry.liess.tv HA_URL=https://homeassistant.liess.tv >> /opt/liessure/.env"
ct 110 "shred -u /root/minted-liessure/grafana.key && rmdir /root/minted-liessure"
for k in $KEYS; do
  ct 163 "test \$(grep -c '^$k=.' /opt/liessure/.env) = 1" || die "$k did not land"
done

say "4. check the tokens from ct 163"
codes="$(ct 163 "$PROBE")" || codes=""
say "    jobs, grafana, home assistant: $codes"
[[ "$codes" == "200 200 200" ]] || die "a token does not work (jobs, grafana, home assistant: $codes)"

say "5. ct 163: the 30 s deploy timer (data, hash-checked)"
cat > "$LOCAL_TMP/liessure-autodeploy.timer" <<'LIESSURE_FILE_EOF'
[Unit]
Description=Check for a new Liessure build every 30 seconds

[Timer]
OnActiveSec=30s
OnUnitInactiveSec=30s
RandomizedDelaySec=5s

[Install]
WantedBy=timers.target
LIESSURE_FILE_EOF
[[ "$(sha "$LOCAL_TMP/liessure-autodeploy.timer")" == 9d8fc1d755780a5b1f04803dc9471b4dc1034044cebffa0f5dcad43e78c2bdff ]] || die "embedded timer is damaged"
push 163 /etc/systemd/system/liessure-autodeploy.timer "$LOCAL_TMP/liessure-autodeploy.timer"
[[ "$(ct 163 "sha256sum /etc/systemd/system/liessure-autodeploy.timer" | cut -d' ' -f1)" == 9d8fc1d755780a5b1f04803dc9471b4dc1034044cebffa0f5dcad43e78c2bdff ]] || die "timer did not land intact"
ct 163 "systemctl daemon-reload && systemctl restart liessure-autodeploy.timer"

say "6. ct 163: recreate Liessure so it reads .env"
ct 163 "cd /opt/liessure && flock /var/lib/liessure-autodeploy/lock docker compose up -d --no-build --force-recreate 2>&1 | tail -3"
ok=""
for i in $(seq 1 30); do
  if ct 163 "curl -fsS -m 3 http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] || die "Liessure did not come back healthy"
ct 163 "curl -fsS -m 10 http://127.0.0.1:8000/api/sources | head -c 1200; echo" || say "    (sources summary unavailable)"
ct 163 "systemctl list-timers --all liessure-autodeploy.timer | head -3"
trap - ERR
say "DONE: jobs, skills, lab health and home cards are on; deploys are checked every 30 s"
