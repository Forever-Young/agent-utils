# agents mobile

The Mac side of the agterm mobile app, a separate project.
The app runs these commands over SSH on the Mac; no port is opened.
Code: `agent_utils/agterm/mobile.py`.
Protocol: [mobile-protocol.md](../mobile-protocol.md).
Related: [remote guide](../guide/remote.md).

```sh
agents mobile ACTION [OPTIONS]
```

| Option | Meaning |
|---|---|
| `ACTION` | `serve` or `attach`. |
| `--config PATH` | Config file for projects and `new_session_position`. Default: `~/.config/agent-utils/config.json`. Used by `serve`. |
| `--session ID` | `attach`: the session id from the tree. Required for `attach`. |
| `--pane PANE` | `attach`: `left` (default) or `right`. |
| `--interval SECONDS` | `serve`: seconds between tree polls after `subscribe`. Default 2.0. A `subscribe` request can set its own interval, limited to 0.5 to 60. |
| `--dry-run` | `serve`: the `launch` method prints the `agtermctl` commands instead of running them. |

### `agents mobile serve`

The control channel: versioned JSON lines (protocol 1) on stdin and stdout. Only
protocol lines go to stdout; progress output goes to stderr. Methods: `hello`, `tree`,
`projects`, `history`, `launch`, `rename`, `type`, `screen`, `subscribe`,
`unsubscribe`. It exits when stdin closes.

```sh
printf '%s\n' '{"id":1,"method":"hello"}' '{"id":2,"method":"tree"}' | ssh dev-mac agents mobile serve
```

### `agents mobile attach`

Attaches one pane on a PTY channel that the app opens, with the same checks and the same
accepted check/attach race as [`agents remote terminal`](remote.md#agents-remote-terminal):
the inventory is re-read, a changed session, pane, daemon or endpoint is refused, and
the bundled `zmx get` check runs immediately before attach. The check does not make
attachment atomic. Without a PTY it exits with an error.

```sh
ssh -t dev-mac agents mobile attach --session EXACT_ORIGIN_ID --pane left
```

### Pair and authorize a native mobile client

`agents mobile pair [--host NAME] [--port 22] [--user USER] [--host-key PATH]`
prints an `agterm-mobile://pair` link containing the host public key and its fingerprint.
Use a reachable local/Tailscale hostname with `--host`. If qrencode is installed a QR
code is printed too. No private key is included.

`agents mobile authorize --key 'ssh-ed25519 …'` previews an authorized_keys entry.
Add `--yes` to write it atomically with a backup. Access is restricted by default;
`--restricted` makes that explicit, `--full` permits an ordinary SSH shell as well.
Existing keys are left as they are; changing their scope requires manual review.

`agents mobile gate` is the forced command for restricted keys. It parses
SSH_ORIGINAL_COMMAND with an allowlist: only `agents mobile serve` and
`agents mobile attach --session ID [--pane left|right]`. It runs no arbitrary shell
text. Forwarding and user rc files are disabled by the authorized_keys restrictions.

The protocol also offers `appearance` (no parameters): bundled theme name, color and
palette maps, tints, and default sidebar density. Custom Ghostty overrides are not
reported yet.
