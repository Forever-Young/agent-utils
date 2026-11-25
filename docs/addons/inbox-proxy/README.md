# Add-on: a stable Inbox address with Caddy and Tailscale

Not part of `agents`; copy and adapt. The Plannotator Inbox listens on a port that
changes on every restart. This add-on keeps one fixed tailnet name pointing at it, so a
phone bookmark keeps working.

Needs: Tailscale with HTTPS certificates, [Caddy](https://caddyserver.com) with its admin
API on `localhost:2019`, and the Inbox published on your tailnet (`inboxTailscale` in
`~/.plannotator/config.json`).

1. Put `Caddyfile.example` into your Caddyfile; set your host name, bind address and
   certificate paths.
2. Preview, then install the sync job:

   ```sh
   ./install.sh --login you@example.com            # preview: prints every step
   ./install.sh --login you@example.com --apply
   ```

   It copies `inbox_caddy_sync.sh` to `~/.local/share/agent-utils/inbox-proxy/`, writes
   `~/Library/LaunchAgents/dev.agent-utils.inbox-caddy-sync.plist` (change the name with
   `--label`) and loads it with `launchctl`. It does not touch your Caddyfile.
   The job runs every minute. It reads `plannotator inbox status --json`, writes
   `~/.plannotator/caddy-upstream.caddy` and reloads Caddy only when the port changed.
   It also clears a stale `tailscale serve` rule left by an unclean stop, and restarts an
   unpublished Inbox at most every 10 minutes.

After an Inbox restart the proxy may answer 502 for up to a minute. Undo: `./install.sh --remove --apply`, then delete the Caddyfile block and
`~/.plannotator/caddy-upstream.caddy`.

To make agents give this address, add a private snippet (see
[the Inbox guide](../../guide/inbox.md)) that names your stable URL.
