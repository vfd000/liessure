#!/usr/bin/env bash
# CHANGES
#   ct 118: read-only preflight: /opt/litellm-gateway/.env has a LITELLM_MASTER_KEY line; /root/minted/liessure.key
#           does not exist yet
#   ct 163: read-only preflight: /opt/liessure/.env has an empty LLM_KEY= line (no key minted before)
#   ct 118: reads LITELLM_MASTER_KEY from /opt/litellm-gateway/.env (never printed) into a 0600 header file under
#           /root/minted, calls its own gateway POST http://127.0.0.1:4000/key/generate once: a virtual key with alias
#           liessure, models [fast-haiku] only, max_budget 5 USD per 30 days, rpm_limit 30; writes the key to
#           /root/minted/liessure.key (0600) and deletes the header file and the raw response
#   ct 163: replaces the LLM_KEY= line in /opt/liessure/.env with the key, piped from ct 118 through wtr (never
#           printed, never written on wtr)
#   ct 118: shreds /root/minted/liessure.key
#   ct 163: two test calls to https://llm.liess.tv/v1/chat/completions with the new key (max_tokens 5): fast-haiku
#           must answer 200, plan-review must be refused (proves the scope); HTTP codes only are printed
#   ct 163: if a liessure container already exists, recreates it (docker compose up -d --force-recreate) so it
#           reads the key; if none exists yet, nothing is started
#   On any failure after the key is minted: deletes the key in the gateway (POST /key/delete by alias liessure),
#   shreds the minted file, and puts back the empty LLM_KEY= line.
# END CHANGES
#
# Liessure's LLM gateway key, minted the way bb scripts/litellm-gateway/mint-scoped-key.sh does it.
set -euo pipefail

WTR="axl@10.11.2.233"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
say() { echo "==> $*"; }
wtr() { ssh "${SSH_OPTS[@]}" "$WTR" "$@" </dev/null; }
host_sh() { wtr "sudo bash -c $(printf %q "$1")"; }
gw() { wtr "sudo pct exec 118 -- bash -c $(printf %q "$1")"; }
app() { wtr "sudo pct exec 163 -- bash -c $(printf %q "$1")"; }
MINTED=0

read -r -d '' MINT <<'CMD' || true
set -euo pipefail
umask 077
mkdir -p /root/minted
mk="$(grep -m1 '^LITELLM_MASTER_KEY=' /opt/litellm-gateway/.env | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
printf 'Authorization: Bearer %s\n' "$mk" > /root/minted/master.hdr
unset mk
code="$(curl -sS --max-time 30 -o /root/minted/liessure.resp -w '%{http_code}' -X POST \
  -H @/root/minted/master.hdr -H 'Content-Type: application/json' \
  --data-binary '{"key_alias":"liessure","models":["fast-haiku"],"max_budget":5,"budget_duration":"30d","rpm_limit":30,"metadata":{"purpose":"Liessure feed curator (CT 163)"}}' \
  http://127.0.0.1:4000/key/generate)"
rm -f /root/minted/master.hdr
grep -o '"key":"sk-[^"]*"' /root/minted/liessure.resp | head -1 | cut -d'"' -f4 > /root/minted/liessure.key
rm -f /root/minted/liessure.resp
test -s /root/minted/liessure.key
echo "key/generate: HTTP $code"
CMD

read -r -d '' REVOKE <<'CMD' || true
umask 077
mk="$(grep -m1 '^LITELLM_MASTER_KEY=' /opt/litellm-gateway/.env | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//')"
printf 'Authorization: Bearer %s\n' "$mk" > /root/minted/master.hdr
unset mk
curl -s --max-time 20 -o /dev/null -w 'key/delete: HTTP %{http_code}\n' -X POST -H @/root/minted/master.hdr \
  -H 'Content-Type: application/json' --data-binary '{"key_aliases":["liessure"]}' http://127.0.0.1:4000/key/delete
rm -f /root/minted/master.hdr /root/minted/liessure.resp
shred -u /root/minted/liessure.key 2>/dev/null || rm -f /root/minted/liessure.key
CMD

read -r -d '' PROBE <<'CMD' || true
k="$(grep -m1 '^LLM_KEY=' /opt/liessure/.env | cut -d= -f2-)"
curl -s --max-time 60 -o /dev/null -w '%{http_code}' -X POST https://llm.liess.tv/v1/chat/completions \
  -H "Authorization: Bearer $k" -H 'Content-Type: application/json' \
  --data-binary '{"model":"__MODEL__","messages":[{"role":"user","content":"Reply with the word ok"}],"max_tokens":5}'
CMD

undo() {
  [[ $MINTED == 1 ]] || return 0
  echo "UNDO: deleting the new key and clearing LLM_KEY" >&2
  gw "$REVOKE" || true
  app "sed -i '/^LLM_KEY=/d' /opt/liessure/.env && echo 'LLM_KEY=' >> /opt/liessure/.env" || true
}
die() { echo "ABORT: $*" >&2; undo; exit 1; }
set -E
trap 'rc=$?; trap - ERR; undo; exit $rc' ERR

say "1. preflight"
[[ "$(wtr "sudo pct exec 163 -- hostname")" == liessure ]] || die "CT 163 is not liessure"
gw "grep -q '^LITELLM_MASTER_KEY=.' /opt/litellm-gateway/.env" || die "no master key line in ct 118"
gw "test ! -e /root/minted/liessure.key" || die "/root/minted/liessure.key already exists in ct 118"
app "grep -qx 'LLM_KEY=' /opt/liessure/.env" || die "/opt/liessure/.env has no empty LLM_KEY= line (already minted?)"

say "2. ct 118: mint the key"
MINTED=1
gw "$MINT"

say "3. move it into ct 163's .env, then shred the minted copy"
app "sed -i '/^LLM_KEY=/d' /opt/liessure/.env"
host_sh "{ printf 'LLM_KEY='; pct exec 118 -- cat /root/minted/liessure.key | tr -d '\r\n'; echo; } | pct exec 163 -- tee -a /opt/liessure/.env >/dev/null"
gw "shred -u /root/minted/liessure.key"
app "test \$(grep -c '^LLM_KEY=sk-' /opt/liessure/.env) = 1" || die "LLM_KEY did not land"

say "4. prove the scope from ct 163"
ok="$(app "${PROBE//__MODEL__/fast-haiku}")" || ok=""
no="$(app "${PROBE//__MODEL__/plan-review}")" || no=""
say "    fast-haiku: $ok (want 200), plan-review: $no (want 401/403)"
[[ "$ok" == 200 ]] || die "the key cannot use fast-haiku ($ok)"
[[ "$no" != 2* ]] || die "the key reaches plan-review ($no): not scoped"

say "5. reload Liessure if it is already running"
if app "docker inspect liessure-liessure-1 >/dev/null 2>&1"; then
  app "cd /opt/liessure && docker compose up -d --no-build --force-recreate 2>&1 | tail -3"
else
  say "    no liessure container yet; the first release will read the key"
fi
trap - ERR
say "DONE: LLM_KEY is set in CT 163 (alias liessure, fast-haiku only, 5 USD per 30 days)"
