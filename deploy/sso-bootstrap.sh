#!/usr/bin/env bash
#
# Turn on Liessure's Zitadel sign-in (liessure.liess.tv). Same shape as bb's
# scripts/hgss/sso-bootstrap.sh: the `liessure-web` OIDC app in bb-services,
# its secret into 1Password first, then three lines into CT 163's
# /opt/liessure/.env, and the container recreated so it reads them.
# Idempotent. Run it yourself (it writes to 1Password, which agents can't):
#
#   ! bash deploy/sso-bootstrap.sh [--dry-run]
#
#   ZITADEL_CLIENT_ID      } `liessure-web` OIDC app in bb-services
#   ZITADEL_CLIENT_SECRET  }   (1Password first: "liessure-web Zitadel OIDC client")
#   ZITADEL_USER_SUBS      axl + Dani (bb docs/household-service-allowlists.md)
#
# Needs: the write-scoped 1Password service account token at
# ~/.config/bb-scripts/op-service-account-token-write, `ssh` to wtr, jq, curl.
set -euo pipefail

ISSUER="${ZITADEL_ISSUER:-https://sso.liess.tv}"
PROJECT_ID="${ZITADEL_PROJECT_ID:-387471562879533059}"   # bb-services
VAULT="${OP_VAULT:-bb-automation}"
BASE="${LIESSURE_BASE:-https://liessure.liess.tv}"
CTID="${LIESSURE_CTID:-163}"
WTR="${WTR_HOST:-axl@10.11.2.233}"
# axl, Dani (bb docs/household-service-allowlists.md).
USER_SUBS="${LIESSURE_USER_SUBS:-387450248433827843,388076739911221251}"

APP_NAME="liessure-web"
APP_ITEM="liessure-web Zitadel OIDC client"
REDIRECT="${BASE}/auth/callback"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

say() { printf '\n== %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
for c in op jq curl ssh; do command -v "$c" >/dev/null || die "$c is not on PATH"; done

if [[ -z "${OP_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
  OP_SERVICE_ACCOUNT_TOKEN="$(cat ~/.config/bb-scripts/op-service-account-token-write)"
fi
export OP_SERVICE_ACCOUNT_TOKEN

# -4: UniFi answers AAAA for *.liess.tv with the 100:: discard prefix.
CURL=(curl -4 -sS --max-time 20)

say "reading the Zitadel admin PAT from 1Password"
PAT="$(op read "op://${VAULT}/zitadel bb-automation pat/credential")"
[[ -n "$PAT" ]] || die "could not read the Zitadel PAT"

zit() {
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    "${CURL[@]}" -X "$method" "${ISSUER}${path}" \
      -H "Authorization: Bearer ${PAT}" -H 'content-type: application/json' -d "$body"
  else
    "${CURL[@]}" -X "$method" "${ISSUER}${path}" -H "Authorization: Bearer ${PAT}"
  fi
}
op_has() { op item get "$1" --vault "$VAULT" >/dev/null 2>&1; }
save_app() { # <client-id> <secret>
  op item create --vault "$VAULT" --category "API Credential" --title "$APP_ITEM" \
    "username=$1" "credential=$2" "issuer[text]=${ISSUER}" "redirect_uri[text]=${REDIRECT}" \
    "notes[text]=Sign-in for liessure.liess.tv. Written by vfd000/liessure deploy/sso-bootstrap.sh" >/dev/null
}

# ------------------------------------------------------------ 1. the app
say "looking for an existing ${APP_NAME} app"
APPS="$(zit POST "/management/v1/projects/${PROJECT_ID}/apps/_search" '{}')"
jq -e '.result' <<<"$APPS" >/dev/null 2>&1 || die "app search failed: $(jq -c . <<<"$APPS" 2>/dev/null || echo "$APPS")"
APP_ID="$(jq -r --arg n "$APP_NAME" '.result[]? | select(.name==$n) | .id' <<<"$APPS")"

if [[ -n "$APP_ID" ]]; then
  CLIENT_ID="$(zit GET "/management/v1/projects/${PROJECT_ID}/apps/${APP_ID}" | jq -r '.app.oidcConfig.clientId')"
  echo "already exists: app ${APP_ID}, client ${CLIENT_ID}"
  if ! op_has "$APP_ITEM"; then
    (( DRY_RUN )) && die "app exists but its secret is not in 1Password; would regenerate"
    say "secret missing from 1Password - regenerating it"
    APP_SECRET="$(zit POST "/management/v1/projects/${PROJECT_ID}/apps/${APP_ID}/oidc_config/_generate_client_secret" '{}' \
      | jq -r '.clientSecret // empty')"
    [[ -n "$APP_SECRET" ]] || die "could not regenerate the client secret"
    save_app "$CLIENT_ID" "$APP_SECRET"
    unset APP_SECRET
  fi
elif (( DRY_RUN )); then
  echo "[dry-run] would create OIDC app ${APP_NAME} (redirect ${REDIRECT})"
  exit 0
else
  say "creating the ${APP_NAME} OIDC app"
  BODY="$(jq -n --arg name "$APP_NAME" --arg redirect "$REDIRECT" --arg base "$BASE/" '{
    name: $name, redirectUris: [$redirect], postLogoutRedirectUris: [$base],
    responseTypes: ["OIDC_RESPONSE_TYPE_CODE"], grantTypes: ["OIDC_GRANT_TYPE_AUTHORIZATION_CODE"],
    appType: "OIDC_APP_TYPE_WEB", authMethodType: "OIDC_AUTH_METHOD_TYPE_BASIC", devMode: false,
    accessTokenType: "OIDC_TOKEN_TYPE_JWT", idTokenRoleAssertion: false,
    idTokenUserinfoAssertion: false, clockSkew: "0s"
  }')"
  CREATED="$(zit POST "/management/v1/projects/${PROJECT_ID}/apps/oidc" "$BODY")"
  CLIENT_ID="$(jq -r '.clientId // empty' <<<"$CREATED")"
  APP_SECRET="$(jq -r '.clientSecret // empty' <<<"$CREATED")"
  [[ -n "$CLIENT_ID" && -n "$APP_SECRET" ]] || die "app creation failed: $(jq -c 'del(.clientSecret)' <<<"$CREATED")"
  say "saving the app credentials to 1Password"
  save_app "$CLIENT_ID" "$APP_SECRET"
  unset APP_SECRET
  echo "saved client ${CLIENT_ID}"
fi
(( DRY_RUN )) && { echo "[dry-run] would set 3 lines in CT ${CTID}:/opt/liessure/.env and recreate Liessure"; exit 0; }

# ------------------------------------------------- 2. into the guest
ct() { ssh -o BatchMode=yes "$WTR" "sudo pct exec ${CTID} -- bash -c $(printf %q "$1")"; }
setline() { # <KEY> ; value on stdin, never on a command line
  ssh -o BatchMode=yes "$WTR" "sudo pct exec ${CTID} -- bash -c 'f=/opt/liessure/.env; sed -i \"/^$1=/d\" \$f; { printf \"%s=\" $1; cat; echo; } >> \$f'"
}
say "writing the SSO values into CT ${CTID}:/opt/liessure/.env"
printf '%s' "$CLIENT_ID" | setline ZITADEL_CLIENT_ID
op read "op://${VAULT}/${APP_ITEM}/credential" | tr -d '\n' | setline ZITADEL_CLIENT_SECRET
printf '%s' "$USER_SUBS" | setline ZITADEL_USER_SUBS
ct "grep -c '^ZITADEL_' /opt/liessure/.env"

say "recreating Liessure so it reads them"
ct "cd /opt/liessure && flock /var/lib/liessure-autodeploy/lock docker compose up -d --no-build --force-recreate 2>&1 | tail -3"
for _ in $(seq 1 30); do
  if ct "curl -fsS -m 3 http://127.0.0.1:8000/healthz" >/dev/null 2>&1; then break; fi
  sleep 2
done
code="$(ct "curl -s -o /dev/null -w '%{http_code}' -m 5 http://127.0.0.1:8000/api/feed")"
echo "signed-out /api/feed answers ${code} (401 means sign-in is on)"
echo
echo "done. Open https://liessure.liess.tv and sign in. Add Liessure to bb's docs/household-service-allowlists.md."
