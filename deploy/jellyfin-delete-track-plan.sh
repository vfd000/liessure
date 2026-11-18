#!/usr/bin/env bash
# CHANGES
#   ct 126: read-only preflight: reads JELLYFIN_API_KEY from /root/mcp-server/.env (never printed);
#           GET https://jellyfin.liess.tv/Items?ids=c823a7bcd8b5d14526f4857d4b7d7c13 prints the item's
#           Name, Album, AlbumArtist and file Path; aborts unless it is "I Drink Wine" by Adele
#   ct 126: DELETE https://jellyfin.liess.tv/Items/c823a7bcd8b5d14526f4857d4b7d7c13 with that key: Jellyfin
#           removes the track from the library AND deletes its audio file from disk. Irreversible: this
#           plan takes no copy of the file (axl asked for it gone for good, 2026-11-17)
#   ct 126: read-only check: the same GET must now return no item
#   wtr: none - only relays commands with pct exec; ssh to axl@10.11.2.233 uses BatchMode and
#        StrictHostKeyChecking=accept-new
# END CHANGES
#
# Permanently remove Adele - "I Drink Wine" (album 30) from the Jellyfin music library.
set -euo pipefail

WTR="axl@10.11.2.233"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
say() { echo "==> $*"; }
ct() { local id="$1"; shift; ssh "${SSH_OPTS[@]}" "$WTR" "sudo pct exec $id -- bash -c $(printf %q "$1")" </dev/null; }

ITEM=c823a7bcd8b5d14526f4857d4b7d7c13

say "1. ct 126: what is item $ITEM? (read-only)"
ct 126 "k=\"\$(grep -m1 '^JELLYFIN_API_KEY=' /root/mcp-server/.env | cut -d= -f2- | sed -e 's/^\"//' -e 's/\"\$//')\"; curl -fsS -m 20 -H \"X-Emby-Token: \$k\" 'https://jellyfin.liess.tv/Items?ids=$ITEM&Recursive=true&Fields=Path' | python3 -c 'import json,sys; it=json.load(sys.stdin).get(\"Items\") or [{}]; i=it[0]; print(i.get(\"Name\"), \"|\", i.get(\"Album\"), \"|\", i.get(\"AlbumArtist\"), \"|\", i.get(\"Path\")); sys.exit(0 if i.get(\"Name\")==\"I Drink Wine\" and \"Adele\" in (i.get(\"AlbumArtist\") or \"\") else 3)'" \
  || { echo "ABORT: item $ITEM is not Adele - I Drink Wine (or Jellyfin did not answer); nothing deleted" >&2; exit 1; }

say "2. ct 126: delete it (library entry and file)"
ct 126 "k=\"\$(grep -m1 '^JELLYFIN_API_KEY=' /root/mcp-server/.env | cut -d= -f2- | sed -e 's/^\"//' -e 's/\"\$//')\"; code=\"\$(curl -s -m 30 -o /dev/null -w '%{http_code}' -X DELETE -H \"X-Emby-Token: \$k\" https://jellyfin.liess.tv/Items/$ITEM)\"; echo \"DELETE /Items: HTTP \$code\"; [ \"\$code\" = 204 ]"

say "3. ct 126: check it is gone (read-only)"
ct 126 "k=\"\$(grep -m1 '^JELLYFIN_API_KEY=' /root/mcp-server/.env | cut -d= -f2- | sed -e 's/^\"//' -e 's/\"\$//')\"; curl -fsS -m 20 -H \"X-Emby-Token: \$k\" 'https://jellyfin.liess.tv/Items?ids=$ITEM&Recursive=true' | python3 -c 'import json,sys; n=len(json.load(sys.stdin).get(\"Items\") or []); print(\"items left with that id:\", n); sys.exit(1 if n else 0)'"

say "DONE: Adele - I Drink Wine is out of the library and off the disk"
