#!/bin/bash
# Keep Caddy's upstream for your Inbox proxy pointed at the Plannotator Inbox's
# current tailnet "door" port. The port changes when the Inbox restarts.
# Writes ~/.plannotator/caddy-upstream.caddy (imported by your Caddyfile)
# and reloads Caddy through its admin API only when the snippet changed.
# Run from launchd (see the launchd template next to this script) every minute.
#
# Self-heals the post-reboot case: tailscaled keeps `tailscale serve` rules
# across restarts, so after an unclean stop (reboot, kill) the new Inbox refuses
# to publish ("Another tailscale serve mapping already uses port ..."). When
# that leftover mapping is a loopback proxy with a dead backend, remove it and
# restart the Inbox. A mapping with a live backend, or one that is not a
# loopback proxy, is never touched: it may be something of the person's own.
#
# Also retries when the Inbox is running but unpublished with nothing blocking
# it and Tailscale is up (the Inbox started before Tailscale connected after a
# reboot): restart it so it publishes again, at most once every 10 minutes.
set -uo pipefail
export PATH="$HOME/.local/bin":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin

SNIPPET="$HOME/.plannotator/caddy-upstream.caddy"
CADDYFILE="${CADDYFILE:-/opt/homebrew/etc/Caddyfile}"
# Your Tailscale login (the account that owns the tailnet). Required.
LOGIN="${INBOX_TAILSCALE_LOGIN:?set INBOX_TAILSCALE_LOGIN to your Tailscale login}"
STAMP="$HOME/.plannotator/caddy-sync-last-retry"

parse_status() {
  # Prints "<pid> <main port> <tailnet host:port>"; nothing when unpublished.
  printf '%s' "$1" | /usr/bin/python3 -I -c '
import json, sys, re
d = json.load(sys.stdin)
m = re.match(r"https://([^/]+)/", d.get("tailnet_url") or "")
if d.get("running") and m:
    print(d["pid"], d["port"], m.group(1))
' 2>/dev/null || true
}

STATUS=$(plannotator inbox status --json 2>/dev/null) || { echo "Inbox not running; leaving Caddy as is" >&2; exit 0; }
read -r PID PORT HOSTPORT <<<"$(parse_status "$STATUS")" || true

if [ -z "${PID:-}" ]; then
  # Inbox is up but not published on the tailnet. The usual cause: a serve
  # rule left from an earlier Inbox run still occupies the Inbox's port,
  # and the Inbox never replaces an existing mapping.
  MAINPORT=$(printf '%s' "$STATUS" | /usr/bin/python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("port") or "")' 2>/dev/null || true)
  TS_OK=0
  SERVE=""
  if SERVE=$(tailscale serve status --json 2>/dev/null); then TS_OK=1; fi
  BACKEND=""
  if [ -n "${MAINPORT:-}" ] && [ -n "$SERVE" ]; then
    BACKEND=$(printf '%s' "$SERVE" | /usr/bin/python3 -I -c '
import json, sys
d = json.load(sys.stdin) or {}
port = sys.argv[1]
for hostport, site in (d.get("Web") or {}).items():
    if hostport.rsplit(":", 1)[-1] == port:
        proxy = ((site.get("Handlers") or {}).get("/") or {}).get("Proxy")
        if proxy:
            print(proxy.split("://", 1)[-1])  # strip scheme: "http://127.0.0.1:55191" -> "127.0.0.1:55191"
        break
' "$MAINPORT" 2>/dev/null || true)
  fi
  if [ -n "$BACKEND" ]; then
    BHOST=${BACKEND%%:*}; BPORT=${BACKEND##*:}
    if [ "$BHOST" = "127.0.0.1" ] && [ -n "$BPORT" ] && [ "$BPORT" -eq "$BPORT" ] 2>/dev/null; then
      if nc -z 127.0.0.1 "$BPORT" 2>/dev/null; then
        echo "Serve mapping on port $MAINPORT -> $BACKEND has a live backend; leaving it alone" >&2
      else
        echo "Stale serve rule on port $MAINPORT -> dead backend $BACKEND; removing it and restarting the Inbox" >&2
        if tailscale serve --https="$MAINPORT" off >/dev/null 2>&1; then
          date +%s > "$STAMP"
          plannotator inbox restart
          STATUS=$(plannotator inbox status --json 2>/dev/null) || STATUS=""
          read -r PID PORT HOSTPORT <<<"$(parse_status "${STATUS:-}")" || true
        else
          echo "Could not remove the serve rule; leaving Caddy as is" >&2
        fi
      fi
    else
      echo "Serve mapping on port $MAINPORT -> $BACKEND is not a loopback host:port proxy; leaving it alone" >&2
    fi
  fi
  # Nothing occupies the Inbox's serve port and Tailscale answers: the Inbox's
  # own publish attempt raced something transient (typically the Inbox starting
  # before Tailscale connected after a reboot). Restart it so it publishes
  # again — at most once every 10 minutes, and only when the Inbox is meant to
  # publish at all (inboxTailscale in ~/.plannotator/config.json).
  if [ "$TS_OK" = 1 ] && [ -z "$BACKEND" ] && [ -n "${MAINPORT:-}" ]; then
    WANTS_TS=$(/usr/bin/python3 -I -c 'import json,sys; print("1" if json.load(open(sys.argv[1])).get("inboxTailscale") else "0")' "$HOME/.plannotator/config.json" 2>/dev/null || echo 0)
    NOW=$(date +%s); LAST=$(cat "$STAMP" 2>/dev/null || echo 0)
    if [ "$WANTS_TS" != "1" ]; then
      echo "Inbox is unpublished but inboxTailscale is off in the config; leaving it alone" >&2
    elif [ $((NOW - LAST)) -lt 600 ]; then
      echo "Inbox unpublished with nothing blocking it; last retry was $((NOW - LAST))s ago, waiting" >&2
    else
      echo "Inbox unpublished with nothing blocking it and Tailscale up; restarting it to publish" >&2
      date +%s > "$STAMP"
      plannotator inbox restart
      STATUS=$(plannotator inbox status --json 2>/dev/null) || STATUS=""
      read -r PID PORT HOSTPORT <<<"$(parse_status "${STATUS:-}")" || true
    fi
  fi
fi

if [ -z "${PID:-}" ]; then
  echo "Inbox is not published on the tailnet; leaving Caddy as is" >&2
  exit 0
fi

# The door is the Inbox's other loopback listener (the one Tailscale serve proxies to).
DOOR=$(lsof -nP -a -p "$PID" -iTCP -sTCP:LISTEN 2>/dev/null | awk -v main="$PORT" '
  NR > 1 { n = split($9, a, ":"); if (a[n] != main && $9 ~ /^127\.0.0\.1:/) { print a[n]; exit } }')
if [ -z "$DOOR" ]; then
  echo "No door port found for pid $PID; leaving Caddy as is" >&2
  exit 0
fi

NEW=$(cat <<EOF
reverse_proxy 127.0.0.1:$DOOR {
	header_up Host $HOSTPORT
	header_up Tailscale-User-Login $LOGIN
	header_up -X-Forwarded-For
}
EOF
)

if [ -f "$SNIPPET" ] && [ "$(cat "$SNIPPET")" = "$NEW" ]; then
  exit 0
fi
printf '%s\n' "$NEW" > "$SNIPPET"
curl -fsS -X POST localhost:2019/load -H 'Content-Type: text/caddyfile' --data-binary @"$CADDYFILE" >/dev/null || { echo "Caddy reload failed" >&2; exit 1; }
echo "$(date '+%F %T') upstream -> 127.0.0.1:$DOOR ($HOSTPORT), Caddy reloaded"
