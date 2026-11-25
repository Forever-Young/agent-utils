# Reach sessions from another Mac or a phone

The **origin** is the Mac where agterm runs the sessions. You can reach them three ways:

| From | How | You get |
| --- | --- | --- |
| another Mac with agterm | `agents remote attach` | the session as a native agterm row, with companions, leadership and resize |
| any SSH client | `agents remote shell` | an ordinary SSH shell on the origin |
| a phone or a plain SSH terminal | `agents remote terminal` on the origin | one pane of one session in that terminal |

Key-based SSH to the origin must already work; aliases and keys stay in your normal
SSH config. Optionally list hosts in `config.json` so the chooser offers them:

```json
"remotes": [{"name": "Development Mac", "host": "dev-mac"}]
```

`agents remote` (or a bare `agents` in an SSH shell) opens a chooser for host and mode.

## Attach from another Mac

```sh
agents remote list --host dev-mac --agents-only
agents remote attach --host dev-mac --attention-first --companions
agents remote attach --host dev-mac --session EXACT_ORIGIN_ID
agents remote detach --session LOCAL_ATTACHED_ROW_ID
agents remote lead --session LOCAL_ATTACHED_ROW_ID
```

Both Macs need agterm running, and the origin's sessions must be Live sessions.
Attaching goes through `agtermctl zmx tree/attach`, which finds the origin's bundled
endpoint and revalidates the session. `--window` and `--project` filter by origin
window and workspace name; `--destination-window` picks the local window. `detach`, or
closing the attached row, detaches only this client. `lead` makes this client the
session's leader. Resize, leadership and reconnect are agterm's.

## From a phone or a plain SSH terminal

On the phone (for example in Termius), SSH to the origin and run `agents`; under SSH it
offers attach, start/resume in agterm, or Zellij. For agterm launches, choose the
window, project, provider and new/resume; the SSH terminal then attaches to the new
row. Zellij asks for a project even when the current directory matches one, then
offers existing tabs or new/resume. Or attach directly:

```sh
ssh -t dev-mac 'agents remote terminal'
agents remote terminal --session EXACT_ORIGIN_ID --pane left    # skip the picker
```

Terminal mode picks one pane per connection. Before attaching it reads the Live
inventory again, checks that the bundled endpoint and daemon are unchanged and runs
`zmx get`. It needs a TTY.

- **Ctrl+\\ detaches only this client.** Do not use `zmx detach`: it detaches every
  client.
- Escape in the picker returns to the SSH shell.
- Companions and splits need a separate connection each. GUI overlays and native
  leadership controls are not available in this mode.
- Raw zmx handles input and resize; test two clients on one session before you rely
  on it.

**Accepted race.** The bundled zmx has no attach-only-if-it-exists mode. If the chosen
daemon disappears between the check and the attach, zmx can create a new daemon with
that name. The check catches targets that are already gone or changed, but the attach
is not atomic. Nothing kills a replacement process automatically.

## The mobile app helper

`agents mobile` is the Mac side of the agterm mobile app, a separate project.
The app runs `ssh dev-mac agents mobile serve` for a JSON-lines control channel (tree,
live updates, projects, launch, rename, type, screen preview) and
`agents mobile attach --session ID --pane left` on a PTY channel per pane, with the same
checks and race as terminal mode. No port is opened. Try the control channel by hand:

```sh
printf '%s\n' '{"id":1,"method":"hello"}' '{"id":2,"method":"tree"}' | ssh dev-mac agents mobile serve
```

`--dry-run` makes `launch` print the `agtermctl` commands instead.
The protocol is in [docs/mobile-protocol.md](../mobile-protocol.md).

Reference: [remote.md](../reference/remote.md), [mobile.md](../reference/mobile.md).
