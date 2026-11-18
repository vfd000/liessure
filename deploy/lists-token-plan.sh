#!/usr/bin/env bash
# CHANGES
#   Step 1, read-only preflight (aborts before any change if a check fails):
#     ct 163: hostname is liessure; /opt/liessure/.env has no LISTS_URL or LISTS_TOKEN line yet
#     ct 125: /home/runner/actions-runner/_work/lists/lists/.env (written by the lists repo's deploy workflow) has an
#             MCP_API_TOKEN line (existence only)
#   ct 125: reads MCP_API_TOKEN from that .env to copy it (never printed). This is the lists service's only machine
#           credential and it can read AND write every list (the service has no read-only token); Liessure only ever
#           sends GET /lists/<id>. Rotating it in the lists repo's GitHub secrets turns off Liessure's to-do cards
#   ct 163: appends to /opt/liessure/.env (0600): LISTS_URL=https://lists.liess.tv and LISTS_TOKEN (that token)
#   ct 163: checks it from inside (HTTP code only): GET https://lists.liess.tv/lists/6?include_checked=false
#   ct 163: recreates the liessure container (docker compose up -d --no-build --force-recreate, holding the autodeploy
#           lock so the timer can't race it), waits for /healthz, prints the first 1400 bytes of /api/sources
#   On any failure after a change: removes the two .env lines and recreates the container again
#   wtr: no files or settings change. Every command in this plan, the read-only checks included, is an ssh to
#   axl@10.11.2.233 with BatchMode and StrictHostKeyChecking=accept-new (a wtr host key seen for the first time is
#   trusted automatically; a changed one fails), running sudo pct exec into the container named above. The copy runs
#   as a sudo bash -c pipeline on wtr: pct exec 125 grep ... | sed ... | pct exec 163 -- tee -a .env, so the token
#   passes through a pipe in wtr's memory (never written to wtr's disk, never printed)
# END CHANGES
#
# Liessure's to-do cards: the lists service token, without anyone typing it.
set -euo pipefail

WTR="axl@10.11.2.233"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
say() { echo "==> $*"; }
wtr() { ssh "${SSH_OPTS[@]}" "$WTR" "$@" </dev/null; }
host_sh() { wtr "sudo bash -c $(printf %q "$1")"; }
ct() { local id="$1"; shift; wtr "sudo pct exec $id -- bash -c $(printf %q "$1")"; }
SRC=/home/runner/actions-runner/_work/lists/lists/.env
CHANGED=0

drop_lines() { ct 163 "cd /opt/liessure && sed -i -e '/^LISTS_URL=/d' -e '/^LISTS_TOKEN=/d' .env"; }
recreate() { ct 163 "cd /opt/liessure && flock /var/lib/liessure-autodeploy/lock docker compose up -d --no-build --force-recreate 2>&1 | tail -3"; }
undo() {
  [[ $CHANGED == 1 ]] || return 0
  echo "UNDO: taking the two .env lines back out" >&2
  drop_lines || true
  recreate || true
}
die() { echo "ABORT: $*" >&2; undo; exit 1; }
set -E
trap 'rc=$?; trap - ERR; undo; exit $rc' ERR

say "1. preflight (read-only)"
[[ "$(wtr "sudo pct exec 163 -- hostname")" == liessure ]] || die "CT 163 is not liessure"
for k in LISTS_URL LISTS_TOKEN; do
  ct 163 "! grep -q '^$k=' /opt/liessure/.env" || die "$k is already in CT 163's .env"
done
ct 125 "grep -q '^MCP_API_TOKEN=.' $SRC" || die "ct 125: no MCP_API_TOKEN in $SRC"

say "2. copy the token into ct 163's .env"
CHANGED=1
host_sh "pct exec 125 -- grep -m1 '^MCP_API_TOKEN=' $SRC | sed -e 's/^MCP_API_TOKEN=/LISTS_TOKEN=/' -e 's/=\"/=/' -e 's/\"\$//' | pct exec 163 -- tee -a /opt/liessure/.env >/dev/null"
ct 163 "printf '%s\n' LISTS_URL=https://lists.liess.tv >> /opt/liessure/.env"
for k in LISTS_URL LISTS_TOKEN; do
  ct 163 "test \$(grep -c '^$k=.' /opt/liessure/.env) = 1" || die "$k did not land"
done

say "3. check it from ct 163"
code="$(ct 163 "curl -s -m 20 -o /dev/null -w '%{http_code}' -H \"Authorization: Bearer \$(grep -m1 '^LISTS_TOKEN=' /opt/liessure/.env | cut -d= -f2-)\" 'https://lists.liess.tv/lists/6?include_checked=false'")" || code=""
say "    lists: $code"
[[ "$code" == 200 ]] || die "the lists token does not work ($code)"

say "4. ct 163: recreate Liessure so it reads .env"
recreate
ok=""
for i in $(seq 1 30); do
  if ct 163 "curl -fsS -m 3 http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] || die "Liessure did not come back healthy"
ct 163 "curl -fsS -m 10 http://127.0.0.1:8000/api/sources | head -c 1400; echo" || say "    (sources summary unavailable)"
trap - ERR
say "DONE: to-do cards are on"
