#!/bin/bash
# Runs on CT 163 (root), hourly: keep a read-only checkout of the bb repo's docs and Claude skills on
# Liessure's data volume, where the container reads it as /data/bbdocs ("Your lab" cards, app/mylab.py).
# Only docs/ and .claude/skills/ are checked out (sparse, blobless). It authenticates with a deploy key
# that is read-only and scoped to vfd000/bb; its private half never leaves this box.
set -euo pipefail

DEST=/var/lib/docker/volumes/liessure_liessure-data/_data/bbdocs
KEY=/root/.ssh/bbdocs_deploy
REPO=git@github.com:vfd000/bb.git
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o IdentityAgent=none -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/root/.ssh/known_hosts_bbdocs"

if [ ! -d "$DEST/.git" ]; then
  rm -rf "$DEST.tmp"
  git clone --quiet --filter=blob:none --sparse "$REPO" "$DEST.tmp"
  git -C "$DEST.tmp" sparse-checkout set docs .claude/skills
  rm -rf "$DEST"
  mv "$DEST.tmp" "$DEST"
else
  git -C "$DEST" fetch --quiet origin main
  git -C "$DEST" reset --quiet --hard origin/main
fi

# When each file last changed, so a card can say how current its doc is.
git -C "$DEST" log --format='@%cs' --name-only -- docs .claude/skills \
  | awk '/^@/ { d = substr($0, 2); next } NF && !($0 in seen) { seen[$0] = 1; print $0 "\t" d }' \
  > "$DEST/.dates.tsv.tmp"
mv "$DEST/.dates.tsv.tmp" "$DEST/.dates.tsv"

chmod -R a+rX "$DEST"
echo "bbdocs at $(git -C "$DEST" rev-parse --short HEAD), $(find "$DEST/docs" "$DEST/.claude/skills" -name '*.md' | wc -l) files"
