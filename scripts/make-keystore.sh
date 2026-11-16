#!/usr/bin/env bash
# One-time: create the Android signing key and load it into the repo's
# GitHub Actions secrets, so every release is signed with the same key and
# Obtainium can update in place.
#
# Run on a machine with keytool (any JDK) and an authenticated `gh`.
# Keep the .jks it leaves behind somewhere safe (1Password): lose it and
# the installed app can't be updated, only uninstalled and reinstalled.
#
#   scripts/make-keystore.sh vfd000/liessure
set -euo pipefail

repo="${1:?usage: $0 owner/repo}"
out="${2:-liessure-release.jks}"
alias="liessure"
[ -e "$out" ] && { echo "$out already exists; refusing to overwrite" >&2; exit 1; }

pass="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32)"

keytool -genkeypair -v -keystore "$out" -storetype PKCS12 \
  -alias "$alias" -keyalg RSA -keysize 4096 -validity 36500 \
  -storepass "$pass" -keypass "$pass" -dname "CN=Liessure"

base64 -w0 "$out" | gh secret set LIESSURE_KEYSTORE_B64 --repo "$repo"
printf %s "$pass"  | gh secret set LIESSURE_KEYSTORE_PASSWORD --repo "$repo"
printf %s "$pass"  | gh secret set LIESSURE_KEY_PASSWORD --repo "$repo"
printf %s "$alias" | gh secret set LIESSURE_KEY_ALIAS --repo "$repo"

echo
echo "Secrets set on $repo. Save $out and this password in 1Password, then delete the file:"
echo "  $pass"
