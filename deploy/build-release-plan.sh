#!/usr/bin/env bash
# Build the deploy-worker plan that releases one published Liessure image to
# CT 163 on wtr. The image comes from .github/workflows/image.yml; its run
# summary prints the exact command:
#
#   bash deploy/build-release-plan.sh ghcr.io/vfd000/liessure@sha256:<digest> <commit> > plan.sh 2> desc.txt
#
# The plan embeds docker-compose.yml from <commit> (hash-checked), points
# /opt/liessure/.env's LIESSURE_IMAGE at the digest, pulls it (public
# package, no credentials) and recreates the container. The digest pins the
# exact image bytes, so nothing runs that the approval didn't name. On a
# failed health check it puts the previous image (and compose file) back.
set -euo pipefail

IMAGE="${1:?ghcr.io/vfd000/liessure@sha256:<digest>}"
COMMIT="${2:?the commit the image was built from}"
[[ "$IMAGE" =~ ^ghcr\.io/vfd000/liessure@sha256:[0-9a-f]{64}$ ]] || { echo "not a pinned ghcr.io/vfd000/liessure digest" >&2; exit 1; }
cd "$(dirname "${BASH_SOURCE[0]}")/.."
COMPOSE="$(git show "$COMMIT:docker-compose.yml")"
CSUM="$(printf '%s\n' "$COMPOSE" | sha256sum | cut -d' ' -f1)"
SHORT="$(git rev-parse --short "$COMMIT")"
grep -q '^LIESSURE_COMPOSE_EOF$' <<<"$COMPOSE" && { echo "marker collision" >&2; exit 1; }

CHANGES="ct 163: read-only preflight: hostname is liessure, /opt/liessure/.env exists, reads its current
        LIESSURE_IMAGE line (an image name, not a secret) and the liessure container's start time, if any
ct 163: installs /opt/liessure/docker-compose.yml from vfd000/liessure $SHORT (data, sha256-checked; the previous
        copy is kept as docker-compose.yml.prev and deleted at the end of a successful run)
ct 163: sets LIESSURE_IMAGE=$IMAGE in /opt/liessure/.env (replacing the previous LIESSURE_IMAGE line, kept for rollback)
ct 163: docker compose pull (that digest, from ghcr.io; public, no credentials) and up -d: (re)creates the liessure
        container (and, on the first release, its network and the liessure-data volume), which runs the new image
        with every setting in .env passed in as its environment; an existing liessure-data volume is kept
ct 163: checks: /healthz answers within 60 s, the container's start time moved and it runs exactly that image;
        then prints the first 1200 bytes of /api/sources (which kinds are configured; no secret values)
ct 163: on failure prints the container's last 40 log lines, puts back the previous compose file and LIESSURE_IMAGE
        and runs up -d again, or, if there was no previous release, docker compose down (the volume is kept)
wtr: none - only relays the commands into ct 163 with pct exec"
{ echo "Release Liessure $SHORT to CT 163 on wtr: image $IMAGE."; echo "CHANGES:"; echo "$CHANGES"; } >&2

echo "#!/usr/bin/env bash"
echo "# CHANGES"
sed 's/^/#   /' <<<"$CHANGES"
echo "# END CHANGES"
cat <<EOF
#
# Liessure release $SHORT -> CT 163. Built by deploy/build-release-plan.sh.
# Check the compose pin: git show $COMMIT:docker-compose.yml | sha256sum
set -euo pipefail

WTR="axl@10.11.2.233"
ID=163
APP=/opt/liessure
IMAGE=$IMAGE
CSUM=$CSUM
EOF
cat <<'EOF'
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
say() { echo "==> $*"; }
wtr() { ssh "${SSH_OPTS[@]}" "$WTR" "$@" </dev/null; }
ct() { wtr "sudo pct exec $ID -- bash -c $(printf %q "$1")"; }
push() { ssh "${SSH_OPTS[@]}" "$WTR" "sudo pct exec $ID -- tee $1" < "$2" >/dev/null; }
sha() { sha256sum < "$1" | cut -d' ' -f1; }
LOCAL_TMP="$(mktemp -d /tmp/liessure-release.XXXXXXXX)"; chmod 700 "$LOCAL_TMP"
trap 'rm -rf "$LOCAL_TMP"' EXIT
CHANGED=0
PREV=""

undo() {
  [[ $CHANGED == 1 ]] || return 0
  echo "UNDO: putting the previous release back" >&2
  ct "cd $APP && if [ -f docker-compose.yml.prev ]; then mv docker-compose.yml.prev docker-compose.yml; fi" || true
  if [[ -n "$PREV" ]]; then
    ct "cd $APP && sed -i '/^LIESSURE_IMAGE=/d' .env && printf 'LIESSURE_IMAGE=%s\n' '$PREV' >> .env && docker compose up -d 2>&1 | tail -3" || true
  else
    ct "cd $APP && sed -i '/^LIESSURE_IMAGE=/d' .env && docker compose down 2>&1 | tail -3" || true
  fi
}
die() { echo "ABORT: $*" >&2; undo; exit 1; }
set -E
trap 'rc=$?; trap - ERR; undo; exit $rc' ERR

say "1. preflight"
[[ "$(wtr "sudo pct exec $ID -- hostname")" == liessure ]] || die "CT $ID is not liessure"
ct "test -f $APP/.env" || die "no $APP/.env"
PREV="$(ct "grep -m1 '^LIESSURE_IMAGE=' $APP/.env | cut -d= -f2-")" || PREV=""
say "    previous image: ${PREV:-none (first release)}"
[[ "$PREV" != "$IMAGE" ]] || { say "already on $IMAGE; nothing to do"; exit 0; }
before="$(ct "docker inspect -f '{{.State.StartedAt}}' liessure-liessure-1 2>/dev/null")" || before=""

say "2. the compose file (data, hash-checked)"
cat > "$LOCAL_TMP/docker-compose.yml" <<'LIESSURE_COMPOSE_EOF'
EOF
printf '%s\n' "$COMPOSE"
cat <<'EOF'
LIESSURE_COMPOSE_EOF
[[ "$(sha "$LOCAL_TMP/docker-compose.yml")" == "$CSUM" ]] || die "embedded docker-compose.yml is damaged"

say "3. ct $ID: install it and point .env at the new image"
CHANGED=1
ct "cd $APP && if [ -f docker-compose.yml ]; then cp -a docker-compose.yml docker-compose.yml.prev; fi"
push "$APP/docker-compose.yml" "$LOCAL_TMP/docker-compose.yml"
[[ "$(ct "sha256sum $APP/docker-compose.yml" | cut -d' ' -f1)" == "$CSUM" ]] || die "docker-compose.yml did not land intact"
ct "cd $APP && sed -i '/^LIESSURE_IMAGE=/d' .env && printf 'LIESSURE_IMAGE=%s\n' '$IMAGE' >> .env"

say "4. ct $ID: pull and start"
ct "cd $APP && docker compose pull 2>&1 | tail -3"
ct "cd $APP && docker compose up -d --no-build 2>&1 | tail -5"
ok=""
for i in $(seq 1 30); do
  if ct "curl -fsS -m 3 http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
[[ -n "$ok" ]] || { ct "cd $APP && docker compose logs --tail=40" || true; die "Liessure did not become healthy"; }
after="$(ct "docker inspect -f '{{.State.StartedAt}}' liessure-liessure-1")" || after=""
[[ -n "$after" && "$after" != "$before" ]] || die "the container did not (re)start (StartedAt $after)"
running="$(ct "docker inspect -f '{{.Config.Image}}' liessure-liessure-1")" || running=""
[[ "$running" == "$IMAGE" ]] || die "the container runs '$running', not $IMAGE"

say "5. check: which sources are on"
ct "curl -fsS -m 10 http://127.0.0.1:8000/api/sources | head -c 1200; echo" || say "    (sources summary unavailable)"
ct "rm -f $APP/docker-compose.yml.prev"
trap - ERR
say "DONE: CT $ID runs $IMAGE"
EOF
