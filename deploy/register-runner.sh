#!/usr/bin/env bash
# Install and register Liessure's GitHub Actions runner inside its CT on wtr.
# Run once, from a workstation logged in to `gh` with ssh to wtr:
#
#   CTID=<liessure's CT id> bash deploy/register-runner.sh
#
# The CT, its `runner` user (locked, in the docker group) and /opt/liessure
# already exist: the deploy-worker plan that created the CT made them. This
# adds only the runner itself. The registration token is minted here with
# your gh login, held in a shell variable, never written to a file or printed,
# and spent at once (it expires in an hour anyway). What stays on the box is
# the runner's own .credentials: it can claim jobs for vfd000/liessure only,
# and is not a repository credential.
#
# After this, the queued `deploy` run (or a re-run of it) builds and starts
# Liessure, and every push to main redeploys it.
#
# Mirrors bb scripts/ci-runner/install-runner.sh (same pinned runner version
# and sha256), without its CI-only tooling.
set -euo pipefail

CTID="${CTID:?set CTID to Liessure's container id}"
REPO="${REPO:-vfd000/liessure}"
RUNNER_NAME="${RUNNER_NAME:-liessure-r7}"
RUNNER_LABELS="${RUNNER_LABELS:-liessure-r7}"
RUNNER_VERSION="${RUNNER_VERSION:-2.337.0}"
RUNNER_SHA256="${RUNNER_SHA256:-70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613}"
PROXMOX_HOST="${PROXMOX_HOST:-axl@10.11.2.233}"
SSH="${SSH:-ssh}"

[[ "$CTID" =~ ^[0-9]+$ ]] || { echo "CTID must be a number" >&2; exit 1; }
in_ct() { $SSH "$PROXMOX_HOST" "sudo -n pct exec $CTID -- bash -s"; }

echo "==> checking CT $CTID is Liessure's"
in_ct <<'CHECK'
set -euo pipefail
[ "$(hostname)" = liessure ] || { echo "this CT is $(hostname), not liessure" >&2; exit 1; }
id runner | grep -q docker || { echo "user runner is missing or not in the docker group" >&2; exit 1; }
test -f /opt/liessure/.env || { echo "/opt/liessure/.env is missing" >&2; exit 1; }
CHECK

echo "==> runner binary ${RUNNER_VERSION}"
in_ct <<BINARY
set -euo pipefail
cd /home/runner
if [ ! -f actions-runner/config.sh ]; then
  tarball="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
  curl -sSfL -o "\$tarball" \\
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/\$tarball"
  echo "${RUNNER_SHA256}  \$tarball" | sha256sum -c -
  mkdir -p actions-runner
  tar xzf "\$tarball" -C actions-runner
  rm -f "\$tarball"
fi
./actions-runner/bin/installdependencies.sh >/dev/null
chown -R runner:runner /home/runner/actions-runner
BINARY

echo "==> registering with $REPO as $RUNNER_NAME"
reg_token="$(gh api -X POST "/repos/$REPO/actions/runners/registration-token" --jq '.token')"
[ -n "$reg_token" ] || { echo "failed to mint a registration token" >&2; exit 1; }
in_ct <<CONFIG
set -euo pipefail
cd /home/runner/actions-runner
if [ -f .runner ]; then
  ./svc.sh stop || true
  ./svc.sh uninstall || true
  su runner -c "./config.sh remove --token '$reg_token'"
fi
su runner -c "./config.sh --unattended --replace \\
  --url https://github.com/$REPO \\
  --token '$reg_token' \\
  --name '$RUNNER_NAME' \\
  --labels '$RUNNER_LABELS' \\
  --work _work"
CONFIG
unset reg_token

echo "==> systemd service"
in_ct <<'SERVICE'
set -euo pipefail
cd /home/runner/actions-runner
./svc.sh install runner
./svc.sh start
sleep 3
systemctl is-active "$(cat ./.service)"
SERVICE

echo "==> done. Re-run the queued 'deploy' workflow on $REPO (or push to main) to start Liessure:"
echo "    gh workflow run deploy --repo $REPO"
