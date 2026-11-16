#!/usr/bin/env bash
# CHANGES
#   wtr: read-only preflight, aborting before any change if it fails: no CT named liessure (pct list); a
#        debian-13-standard template on local (pveam list); a storage named nvme (pvesm status); ct 106 has
#        an MCP_API_TOKEN line and ct 153 a non-empty token file (existence only, no value read)
#   wtr: picks the first guest id from 163 to 199 with no CT, no VM, no guests/<id>.yml and no <id>.fw
#   wtr: writes /etc/pve/firewall/<id>.fw BEFORE the CT exists: TCP 8000 from NPM 10.11.14.18 only, dropped otherwise
#   wtr: creates CT <id> 'liessure' (Debian 13 template from storage local, unprivileged, nesting=1,keyctl=1,
#        2 cores, 1024 MB, 512 MB swap, 8 GB on nvme, vmbr0 VLAN tag 2, DHCP, firewall=1, onboot, tag liessure,
#        timezone America/Los_Angeles)
#        and starts it, then waits up to 60 s for its DNS (getent hosts deb.debian.org), else rolls back
#   ct <id>: apt installs docker.io docker-cli docker-compose curl ca-certificates rsync git
#   ct <id>: creates a locked user 'runner' in the docker group (its GitHub runner is registered later, by hand,
#            with deploy/register-runner.sh - not by this plan) and /opt/liessure (owner runner)
#   ct <id>: writes /opt/liessure/.env (0600, runner): non-secret settings, plus two tokens copied in:
#            GRAPH_TOKEN from ct 106's /opt/household-graph/.env (MCP_API_TOKEN) and
#            TRAVEL_TOKEN from ct 153's /etc/travel/MCP_API_TOKEN, piped through wtr, never printed or stored there
#   ct 106: reads one line of /opt/household-graph/.env (nothing written)
#   ct 153: reads /etc/travel/MCP_API_TOKEN (nothing written)
#   ct <id>: read-only checks after: one GRAPH_TOKEN and one TRAVEL_TOKEN line, .env is 600 runner,
#            and its IP (hostname -I) for the final message
#   Installs no app code and starts no app: the runner deploys Liessure once registered.
#   Rollback on any failure after the CT is created: pct stop + destroy <id>, rm <id>.fw.
# END CHANGES
#
# Liessure (vfd000/liessure) - its own CT on wtr. After this:
#   1. axl, from the laptop: register the runner (deploy/register-runner.sh with CTID=<id>)
#   2. the deploy workflow builds and starts the app (docker compose) in /opt/liessure
#   3. a second plan mints the LLM gateway key into .env
#   4. axl adds the NPM host liessure.liess.tv -> <ip>:8000 and the Jellyfin/Immich/RomM keys to .env
set -euo pipefail

WTR="axl@10.11.2.233"
NPM_IP=10.11.14.18
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
say() { echo "==> $*"; }
wtr() { ssh "${SSH_OPTS[@]}" "$WTR" "$@" </dev/null; }
host_sh() { wtr "sudo bash -c $(printf %q "$1")"; }
ID=""
ct() { wtr "sudo pct exec $ID -- bash -c $(printf %q "$1")"; }
CREATED=0

undo() {
  [[ -n "$ID" ]] || return 0
  echo "UNDO: removing CT $ID and its firewall file" >&2
  if [[ $CREATED == 1 ]]; then
    wtr "sudo pct stop $ID" || true
    wtr "sudo pct destroy $ID --purge 1" || true
  fi
  wtr "sudo rm -f /etc/pve/firewall/$ID.fw" || true
}
die() { echo "ABORT: $*" >&2; undo; exit 1; }
set -E
trap 'rc=$?; trap - ERR; undo; exit $rc' ERR

say "1. preflight"
! wtr "sudo pct list" | awk 'NR>1{print $NF}' | grep -qx liessure || { echo "ABORT: a CT named liessure already exists" >&2; exit 1; }
TEMPLATE="$(wtr "sudo pveam list local" | awk '/debian-13-standard_.*_amd64/{print $1}' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || { echo "ABORT: no debian-13-standard template on storage local" >&2; exit 1; }
wtr "sudo pvesm status" | awk '{print $1}' | grep -qx nvme || { echo "ABORT: no storage named nvme" >&2; exit 1; }
wtr "sudo pct exec 106 -- grep -q '^MCP_API_TOKEN=.' /opt/household-graph/.env" || { echo "ABORT: no MCP_API_TOKEN in ct 106" >&2; exit 1; }
wtr "sudo pct exec 153 -- test -s /etc/travel/MCP_API_TOKEN" || { echo "ABORT: no travel token in ct 153" >&2; exit 1; }
# Proxmox's own nextid offers gaps (105) that wtr's ansible has reserved, so
# pick past the last defined guest instead.
CAND=""
for c in $(seq 163 199); do
  wtr "sudo pct status $c" >/dev/null 2>&1 && continue
  wtr "sudo qm status $c" >/dev/null 2>&1 && continue
  wtr "test -e /opt/bb-ansible/wtr/guests/$c.yml" && continue
  wtr "test -e /etc/pve/firewall/$c.fw" && continue
  CAND="$c"; break
done
[[ -n "$CAND" ]] || { echo "ABORT: no free guest id between 163 and 199" >&2; exit 1; }
say "    ok: id $CAND, template $TEMPLATE"
ID="$CAND"

say "2. wtr: firewall for CT $ID (8000 from NPM only), before the CT exists"
host_sh "printf '%s\n' '[OPTIONS]' '' 'enable: 1' '' '[RULES]' '' 'IN ACCEPT -source $NPM_IP -p tcp -dport 8000 # NPM only (liessure.liess.tv)' 'IN DROP -p tcp -dport 8000' > /etc/pve/firewall/$ID.fw"
wtr "sudo cat /etc/pve/firewall/$ID.fw"

say "3. wtr: create and start CT $ID"
wtr "sudo pct create $ID $TEMPLATE --hostname liessure --cores 2 --memory 1024 --swap 512 --rootfs nvme:8 --unprivileged 1 --features nesting=1,keyctl=1 --net0 name=eth0,bridge=vmbr0,firewall=1,ip=dhcp,tag=2,type=veth --ostype debian --onboot 1 --timezone America/Los_Angeles --tags liessure"
CREATED=1
wtr "sudo pct start $ID"
ok=""
for i in $(seq 1 30); do
  if ct "getent hosts deb.debian.org >/dev/null"; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] || die "CT $ID has no network after 60 s"

say "4. ct $ID: packages, runner user, /opt/liessure"
ct "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq --no-install-recommends docker.io docker-cli docker-compose curl ca-certificates rsync git >/dev/null"
ct "docker compose version"
ct "id runner >/dev/null 2>&1 || useradd --create-home --shell /bin/bash --groups docker runner; passwd -l runner >/dev/null"
ct "install -d -o runner -g runner -m 0755 /opt/liessure"

say "5. ct $ID: /opt/liessure/.env (0600 runner)"
ct "install -o runner -g runner -m 0600 /dev/null /opt/liessure/.env"
ct "printf '%s\n' \
  '# Liessure settings. Read by docker compose. Secrets are filled in by plans or by hand; never commit this file.' \
  'GRAPH_URL=https://graph.liess.tv' \
  'GRAPH_PERSON=Axl Liess' \
  'TRAVEL_URL=https://travel.liess.tv' \
  'LLM_URL=https://llm.liess.tv' \
  'LLM_KEY=' \
  'LANGUAGE_PRACTICE_URL=https://practice.liess.tv' \
  'JELLYFIN_URL=https://jellyfin.liess.tv' \
  'JELLYFIN_TOKEN=' \
  'JELLYFIN_USER_ID=' \
  'IMMICH_URL=https://photos.liess.tv' \
  'IMMICH_API_KEY=' \
  'ROMM_URL=https://games.liess.tv' \
  'ROMM_TOKEN=' \
  '# Invidious is down (502); set INVIDIOUS_URL=https://watch.liess.tv once it is back and a watch token exists' \
  'INVIDIOUS_URL=' \
  'INVIDIOUS_WATCH_TOKEN=' \
  >> /opt/liessure/.env"
host_sh "pct exec 106 -- grep -m1 '^MCP_API_TOKEN=' /opt/household-graph/.env | sed 's/^MCP_API_TOKEN=/GRAPH_TOKEN=/' | pct exec $ID -- tee -a /opt/liessure/.env >/dev/null"
host_sh "{ printf 'TRAVEL_TOKEN='; pct exec 153 -- cat /etc/travel/MCP_API_TOKEN | tr -d '\r\n'; echo; } | pct exec $ID -- tee -a /opt/liessure/.env >/dev/null"
ct "test \$(grep -c '^GRAPH_TOKEN=.' /opt/liessure/.env) = 1" || die "GRAPH_TOKEN did not land"
ct "test \$(grep -c '^TRAVEL_TOKEN=.' /opt/liessure/.env) = 1" || die "TRAVEL_TOKEN did not land"
ct "test \"\$(stat -c '%a %U' /opt/liessure/.env)\" = '600 runner'" || die ".env permissions are wrong"

say "6. checks from inside CT $ID (codes only, no values printed)"
g="$(ct "t=\$(grep -m1 '^GRAPH_TOKEN=' /opt/liessure/.env | cut -d= -f2-); curl -s -o /dev/null -w '%{http_code}' -m 15 -H \"Authorization: Bearer \$t\" https://graph.liess.tv/api/profile/Axl%20Liess")" || g=""
t="$(ct "t=\$(grep -m1 '^TRAVEL_TOKEN=' /opt/liessure/.env | cut -d= -f2-); curl -s -o /dev/null -w '%{http_code}' -m 15 -H \"Authorization: Bearer \$t\" https://travel.liess.tv/api/trips")" || t=""
say "    graph profile: $g, travel trips: $t"
[[ "$g" == 200 ]] || die "the graph did not accept the copied token from CT $ID ($g)"
[[ "$t" == 200 ]] || die "travel did not accept the copied token from CT $ID ($t)"
IP="$(ct "hostname -I" | awk '{print $1}')" || IP="?"
trap - ERR
say "DONE: CT $ID liessure at $IP (8000 open to NPM only). Next: register its runner from the laptop (deploy/register-runner.sh with CTID=$ID)"
