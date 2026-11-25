#!/bin/bash
# Install the Inbox proxy sync job. Prints every step; changes nothing without --apply.
#   install.sh --login you@example.com [--label LABEL] [--apply]
#   install.sh --remove [--label LABEL] [--apply]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="dev.agent-utils.inbox-caddy-sync"
LOGIN=""; APPLY=0; REMOVE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --login) LOGIN="$2"; shift 2;;
    --label) LABEL="$2"; shift 2;;
    --apply) APPLY=1; shift;;
    --remove) REMOVE=1; shift;;
    -h|--help) sed -n 2,5p "$0"; exit 0;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done
DEST="$HOME/.local/share/agent-utils/inbox-proxy"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_=$(id -u)

run() { echo "  -> $*"; if [ "$APPLY" = 1 ]; then "$@"; fi; }
[ "$APPLY" = 1 ] && echo "Applying:" || echo "Preview (nothing changes; add --apply):"

if [ "$REMOVE" = 1 ]; then
  echo "1. Stop the launchd job $LABEL"
  run launchctl bootout "gui/$UID_/$LABEL" || true
  echo "2. Delete $PLIST and $DEST"
  run rm -f "$PLIST"
  run rm -rf "$DEST"
  echo "Left alone: your Caddyfile, ~/.plannotator/caddy-upstream.caddy and Caddy itself."
  exit 0
fi

[ -n "$LOGIN" ] || { echo "--login is required (your Tailscale login, e.g. you@example.com)" >&2; exit 2; }
echo "1. Create $DEST and copy inbox_caddy_sync.sh there"
run mkdir -p "$DEST"
run cp "$HERE/inbox_caddy_sync.sh" "$DEST/inbox_caddy_sync.sh"
run chmod +x "$DEST/inbox_caddy_sync.sh"
echo "2. Write $PLIST: runs the script at load and every 60 s, with your login; log in /tmp/inbox-caddy-sync.log"
if [ "$APPLY" = 1 ]; then
  mkdir -p "$(dirname "$PLIST")"
  sed -e "s|com.example.inbox-caddy-sync|$LABEL|" -e "s|/path/to/inbox_caddy_sync.sh|$DEST/inbox_caddy_sync.sh|" \
      -e "s|you@example.com|$LOGIN|" "$HERE/com.example.inbox-caddy-sync.plist" > "$PLIST"
else
  echo "  -> (would write the plist from com.example.inbox-caddy-sync.plist)"
fi
echo "3. Load the job with launchctl"
run launchctl bootout "gui/$UID_/$LABEL" 2>/dev/null || true
run launchctl bootstrap "gui/$UID_" "$PLIST"
cat <<MSG
Not done by this script (edit yourself):
  - Add the block from Caddyfile.example to your Caddyfile (host name, bind address, certificates).
  - The job writes ~/.plannotator/caddy-upstream.caddy and reloads Caddy through localhost:2019.
Undo: $0 --remove --label $LABEL --apply
MSG
