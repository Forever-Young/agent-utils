# agents remote

Reach agterm sessions on another Mac: an ordinary SSH shell, native Mac-to-Mac
attachment of an origin session into the local agterm window, or terminal-only
attachment of one origin pane from an SSH shell such as a phone. Code:
`agent_utils/agterm/remote.py`. Walk-through: [remote guide](../guide/remote.md).

Requirements: key-based SSH to the origin already works (aliases and auth stay in your
SSH config), agterm runs on both Macs, and the origin has Live sessions. Native
attachment delegates to `agtermctl zmx tree` and `agtermctl zmx attach`, which discover
the origin's bundled zmx endpoint and revalidate the selected session.

```sh
agents remote [ACTION] [OPTIONS]
```

| Option | Meaning |
|---|---|
| `ACTION` | `menu` (default), `list`, `attach`, `terminal`, `shell`, `detach` or `lead`. Each is described below. |
| `--config PATH` | Config file that holds `remotes`. Default: `~/.config/agent-utils/config.json`. |
| `--host HOST` | SSH destination or alias of the origin Mac. No whitespace, no leading dash. Without it, `menu`, `attach` and `shell` offer the hosts from `remotes`. |
| `--window NAME` | Show only origin sessions in this origin window name. |
| `--project NAME` | Show only origin sessions in this origin workspace (project) name. |
| `--destination-window ID` | Local window ID for `attach`, `detach` and `lead`. Default: the active window. |
| `--session ID` | Exact origin session ID for `attach` and `terminal` (skips the picker). For `detach` and `lead`: the local attached row ID. |
| `--attention` | Show only sessions whose status is `blocked` or `completed`. |
| `--attention-first` | Sort `blocked` and `completed` sessions first. |
| `--agents-only` | Show only sessions whose pane foreground process is a known agent CLI. |
| `--companions` | `attach` also attaches the session's companions. |
| `--pane PANE` | `left` or `right`: the origin pane for `terminal`. Without it a split session asks. Refused by every other action. |
| `--dry-run` | Print what would run; see each action. |

## The `remotes` config key

Optional top-level array in `config.json`. Each entry needs `host` (validated like
`--host`); `name` is the label in the chooser and defaults to the host.

```json
{"remotes": [{"name": "Development Mac", "host": "dev-mac"}]}
```

## Actions

### `agents remote menu`

The default. Over SSH (`SSH_TTY` or `SSH_CONNECTION` set) without `--host` it runs
a chooser: **Attach an existing agterm session**, **Start or resume an agterm session**,
or **Start or attach a Zellij session**. The agterm launch path asks for window,
project, provider and new/resume, then attaches this SSH terminal to the created
row using the usual pane checks. Cancellation launches or attaches nothing further.
Zellij uses its project/provider/new/resume menu. Otherwise it asks for a host from `remotes` (unless `--host` is given), then
for **Existing agterm session** (`attach`) or **Ordinary SSH shell** (`shell`).

```sh
agents remote
```

### `agents remote list`

Prints the filtered origin sessions as JSON. Without `--host` it lists this Mac's own
Live sessions.

```sh
agents remote list --host dev-mac --agents-only
```

### `agents remote attach`

Native Mac-to-Mac attachment: runs `agtermctl zmx attach HOST SESSION` with
`--window ID` and `--companions` when given. Without `--session` it lists the filtered
origin sessions in a picker. With `--dry-run` it prints the `agtermctl` commands.
Leadership, resize and reconnect use agterm's backend.

```sh
agents remote attach --host dev-mac --attention-first --companions
agents remote attach --host dev-mac --session EXACT_ORIGIN_ID --destination-window LOCAL_WINDOW_ID
```

### `agents remote terminal`

Terminal-only attachment of one pane, run **on the origin Mac** inside an SSH shell
(phone, Termius, `ssh -t`). It needs a TTY and refuses `--host`,
`--destination-window` and `--companions`. With `--dry-run` it describes the steps and
attaches nothing.

```sh
ssh -t dev-mac 'agents remote terminal'
agents remote terminal --session EXACT_ORIGIN_ID --pane left
```

Steps: read the local Live inventory, pick a session and a pane (one pane per
connection), re-read the inventory and refuse when the session, pane, daemon or bundled
endpoint changed, run the bundled `zmx get DAEMON` check, then `zmx attach DAEMON`.
Variables starting with `ZMX_`, `AGTERM_` or `AGT_` are removed from the zmx
environment and `ZMX_DIR` is set to the endpoint's socket directory.

**Ctrl+\ detaches only this client.** Do not use `zmx detach`; it detaches every
client. Companions and splits need separate connections. This mode does not mirror GUI
overlays or provide native leadership controls; raw zmx owns input and resize.

**Accepted race.** The bundled zmx has no existing-only attach. If the selected daemon
disappears between the immediate `zmx get` check and the attach, zmx can create a fresh
daemon under that name. The check catches targets that are already missing or changed;
it does not make attachment atomic. Nothing kills a replacement process automatically.

### `agents remote shell`

Runs `ssh HOST` without the local `AGTERM_*` and `AGT_*` variables. With `--dry-run` it
prints the `ssh` command.

```sh
agents remote shell --host dev-mac
```

### `agents remote detach`

Closes a local attached row, which detaches only this client; the origin session keeps
running. Needs `--session` with the local row ID. It refuses a row without a remote
host, so it never closes an origin row. `--dry-run` skips that check and prints the
`agtermctl session close` command.

```sh
agents remote detach --session LOCAL_ATTACHED_ROW_ID
```

### `agents remote lead`

Makes this client the leader of a local attached row (`agtermctl session lead`), with
the same `--session` rule and check as `detach`.

```sh
agents remote lead --session LOCAL_ATTACHED_ROW_ID
```
