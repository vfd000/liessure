#!/usr/bin/env bash
# Build the deploy-worker plan that turns on deploy-on-merge for Liessure:
# installs deploy/autodeploy/* in CT 163 and enables the timer.
#
#   bash deploy/build-autodeploy-plan.sh > plan.sh 2> desc.txt
#
# After it runs, every build of main (ghcr.io/vfd000/liessure:latest) goes out
# within a few minutes, health-checked and rolled back on failure, without a
# plan per release. docker-compose.yml changes still need build-release-plan.sh.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/autodeploy"

declare -A DEST=(
  [liessure-autodeploy]=/usr/local/sbin/liessure-autodeploy
  [liessure-autodeploy.service]=/etc/systemd/system/liessure-autodeploy.service
  [liessure-autodeploy.timer]=/etc/systemd/system/liessure-autodeploy.timer
)
FILES=(liessure-autodeploy liessure-autodeploy.service liessure-autodeploy.timer)
for f in "${FILES[@]}"; do
  grep -q '^LIESSURE_FILE_EOF$' "$f" && { echo "marker collision in $f" >&2; exit 1; }
done
sum() { sha256sum "$1" | cut -d' ' -f1; }

CHANGES="ct 163: read-only preflight: hostname is liessure, systemd and docker are present, /opt/liessure/.env exists
ct 163: installs /usr/local/sbin/liessure-autodeploy (0755) and /etc/systemd/system/liessure-autodeploy.service
        and .timer (0644), all data from deploy/autodeploy/ in vfd000/liessure, sha256-checked; replaces earlier copies
ct 163: systemctl daemon-reload; enable --now liessure-autodeploy.timer. From two minutes after this plan, and every
        two minutes after that, the timer runs liessure-autodeploy, which: docker pull ghcr.io/vfd000/liessure:latest
        (public, no credentials; built only from main); if its digest differs from LIESSURE_IMAGE in
        /opt/liessure/.env, rewrites that line to the new digest and runs docker compose up -d (recreates the
        liessure container), then waits up to 60 s for /healthz on that image; if unhealthy, puts the previous
        LIESSURE_IMAGE back, recreates again and records the bad digest in /var/lib/liessure-autodeploy/failed so it
        isn't retried; on success appends to /var/lib/liessure-autodeploy/deployed and prunes images unused for 7 days.
        This is unattended: later merges to main deploy without a plan or approval (axl asked for that, 2026-11-18).
        Its first run deploys the current build of main.
wtr: none - only relays commands into ct 163 with pct exec"
{ echo "Deploy-on-merge for Liessure: a timer in ct 163 that deploys each new build of main, health-checked, with rollback. wtr: relays only."; echo "CHANGES:"; echo "$CHANGES"; } >&2

echo "#!/usr/bin/env bash"
echo "# CHANGES"
sed 's/^/#   /' <<<"$CHANGES"
echo "# END CHANGES"
cat <<'EOF'
#
# Deploy-on-merge for Liessure (CT 163). Built by deploy/build-autodeploy-plan.sh.
set -euo pipefail

WTR="axl@10.11.2.233"
ID=163
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
say() { echo "==> $*"; }
wtr() { ssh "${SSH_OPTS[@]}" "$WTR" "$@" </dev/null; }
ct() { wtr "sudo pct exec $ID -- bash -c $(printf %q "$1")"; }
push() { ssh "${SSH_OPTS[@]}" "$WTR" "sudo pct exec $ID -- tee $1" < "$2" >/dev/null; }
sha() { sha256sum < "$1" | cut -d' ' -f1; }
LOCAL_TMP="$(mktemp -d /tmp/liessure-autodeploy.XXXXXXXX)"; chmod 700 "$LOCAL_TMP"
trap 'rm -rf "$LOCAL_TMP"' EXIT

say "1. preflight"
[[ "$(wtr "sudo pct exec $ID -- hostname")" == liessure ]] || { echo "ABORT: CT $ID is not liessure" >&2; exit 1; }
ct "command -v systemctl >/dev/null && command -v docker >/dev/null && test -f /opt/liessure/.env" \
  || { echo "ABORT: CT $ID lacks systemd, docker or /opt/liessure/.env" >&2; exit 1; }

say "2. the files (data, hash-checked)"
EOF
for f in "${FILES[@]}"; do
  echo "cat > \"\$LOCAL_TMP/$f\" <<'LIESSURE_FILE_EOF'"
  cat "$f"
  echo "LIESSURE_FILE_EOF"
  echo "[[ \"\$(sha \"\$LOCAL_TMP/$f\")\" == $(sum "$f") ]] || { echo \"ABORT: embedded $f is damaged\" >&2; exit 1; }"
done
echo
echo 'say "3. ct 163: install them"'
for f in "${FILES[@]}"; do
  echo "push ${DEST[$f]} \"\$LOCAL_TMP/$f\""
  echo "[[ \"\$(ct \"sha256sum ${DEST[$f]}\" | cut -d' ' -f1)\" == $(sum "$f") ]] || { echo \"ABORT: ${DEST[$f]} did not land intact\" >&2; exit 1; }"
done
cat <<'EOF'
ct "chmod 0755 /usr/local/sbin/liessure-autodeploy && chmod 0644 /etc/systemd/system/liessure-autodeploy.service /etc/systemd/system/liessure-autodeploy.timer"

say "4. ct 163: enable the timer (its first run is two minutes from now)"
ct "systemctl daemon-reload && systemctl enable --now liessure-autodeploy.timer"
ct "systemctl list-timers --all liessure-autodeploy.timer | head -3"
say "DONE: CT 163 deploys each new build of main; journalctl -u liessure-autodeploy shows each run"
EOF
