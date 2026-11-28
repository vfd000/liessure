#!/bin/bash
# Run by axl from the laptop, once: give CT 163 (Liessure, on wtr) read-only access to vfd000/bb's docs.
#   1. a deploy key generated ON CT 163 (the private half never leaves it), added to vfd000/bb READ-ONLY;
#   2. the hourly refresh timer (refresh.sh) that keeps docs/ and .claude/skills/ on the data volume.
# Idempotent: rerunning reuses the key and the deploy key entry. Undo:
#   gh api repos/vfd000/bb/keys --jq '.[] | select(.title | startswith("liessure CT 163")) | .id'
#   gh api -X DELETE repos/vfd000/bb/keys/<id>
# and on CT 163: systemctl disable --now liessure-bbdocs.timer; rm -rf .../_data/bbdocs
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CT=163
TITLE="liessure CT 163 (wtr): bb docs, read-only"

r() { env -u SSH_AUTH_SOCK ssh wtr "$@"; }

# GitHub's host key, pinned in known_hosts: check it against what github.com presents right now.
LIVE="$(ssh-keyscan -T 10 -t ed25519 github.com 2>/dev/null | grep -v '^#' | awk '{print $3}')"
PINNED="$(awk '{print $3}' "$HERE/known_hosts")"
if [ "$LIVE" != "$PINNED" ]; then
  echo "github.com's ed25519 host key isn't the pinned one; stopping." >&2
  exit 1
fi

echo "== deploy key on CT $CT"
r "pct exec $CT -- sh -c 'test -f /root/.ssh/bbdocs_deploy || ssh-keygen -q -t ed25519 -N \"\" -C liessure-bbdocs-readonly -f /root/.ssh/bbdocs_deploy'"
PUB="$(r "pct exec $CT -- cat /root/.ssh/bbdocs_deploy.pub")"
KEYBODY="$(echo "$PUB" | awk '{print $2}')"
if gh api repos/vfd000/bb/keys --jq '.[].key' | grep -qF "$KEYBODY"; then
  echo "already a deploy key on vfd000/bb"
else
  gh api -X POST repos/vfd000/bb/keys -f title="$TITLE" -f key="$PUB" -F read_only=true --jq '"added deploy key \(.id), read_only=\(.read_only)"'
fi

echo "== refresh timer on CT $CT"
for f in refresh.sh liessure-bbdocs.service liessure-bbdocs.timer known_hosts; do
  scp -q "$HERE/$f" "wtr:/tmp/bbdocs-$f"
done
r "pct push $CT /tmp/bbdocs-refresh.sh /usr/local/sbin/liessure-bbdocs-refresh --perms 0755 \
  && pct push $CT /tmp/bbdocs-liessure-bbdocs.service /etc/systemd/system/liessure-bbdocs.service \
  && pct push $CT /tmp/bbdocs-liessure-bbdocs.timer /etc/systemd/system/liessure-bbdocs.timer \
  && pct push $CT /tmp/bbdocs-known_hosts /root/.ssh/known_hosts_bbdocs --perms 0644 \
  ; rm -f /tmp/bbdocs-refresh.sh /tmp/bbdocs-liessure-bbdocs.service /tmp/bbdocs-liessure-bbdocs.timer /tmp/bbdocs-known_hosts"
r "pct exec $CT -- sh -c 'systemctl daemon-reload && systemctl enable --now liessure-bbdocs.timer && systemctl start liessure-bbdocs.service; journalctl -u liessure-bbdocs.service -n 3 --no-pager'"
echo "== done"
