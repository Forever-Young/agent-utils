# `agents mobile` protocol (version 1)

The Mac side of the agterm mobile app. The app opens one SSH connection to the
Mac and uses two kinds of channels on it:

- Control: `agents mobile serve` (exec channel, no PTY). JSON lines in both directions.
- Terminal: `agents mobile attach --session ID [--pane left|right]` (PTY channel). Raw
  terminal bytes; window size through SSH window-change requests. Close the channel to
  detach; never send Ctrl+\ or `zmx detach` (that detaches every client).

No port is opened. SSH keys and host-key checks are the only authentication. Requires a
running agterm with Live sessions on the Mac.

## Framing

One JSON object per line, UTF-8. Lines longer than 1 MiB are refused. Stdout carries
protocol lines only; progress and diagnostics go to stderr.

Request: `{"id": 7, "method": "tree", "params": {}}`. `id` is any JSON value and comes back
unchanged; `params` is optional.

Reply: `{"id": 7, "ok": true, "result": ...}` or
`{"id": 7, "ok": false, "code": "gone", "error": "text"}`.

Event (after `subscribe`): `{"event": "tree.changed", "result": TREE}` or
`{"event": "error", "error": "text"}`.

Error codes:

| Code | Meaning |
| --- | --- |
| `bad_request` | Not JSON, wrong shape, unknown project, invalid argument |
| `unknown_method` | Method not in `hello.methods` |
| `gone` | The session is not in a fresh tree (or has no live pane); refresh and choose again |
| `needs_root` | The agent takes one root and the project has several; pass `root` |
| `failed` | agtermctl, git or the launch failed; `error` has the reason |

## Methods

| Method | Params | Result |
| --- | --- | --- |
| `hello` | none | `protocol`, `host`, `agterm` version, `methods` |
| `appearance` | none | `{theme, colors, palette, tints, density}` for the bundled Mac theme (custom overrides are not included) |
| `tree` | none | `TREE` (below) |
| `subscribe` | `interval` seconds (0.5-60, default 2) | `{interval}`; then `tree.changed` whenever the tree differs, first one at once |
| `unsubscribe` | none | `{}` |
| `projects` | none | `[{id, name, window, default_agent, roots, agents: [{id, name}]}]`, agents in menu order |
| `history` | `project`, `agent`, `limit` (default 50) | `[{conversation_id, title}]` for Claude, Codex and OpenCode; `[]` otherwise |
| `launch` | `project`, `agent`, `mode` (`new`/`resume`/`task`), `session` (resume id), `task`, `name`, `prompt`, `root` | `{record, session, conversation_id}` |
| `rename` | `session`, `name` | `{session, name}` |
| `type` | `session`, `text` (max 16384 chars), `pane`, `enter` (bool, adds CR) | `{session, typed}` |
| `screen` | `session`, `pane`, `lines` (1-2000, default 80) | `{session, pane, text}` plain text |

`launch` uses the same code as `agents agterm new|resume|task`: managed registry record,
sidebar decoration and the configured new-session position. `resume` without `session`
opens the provider's own picker in the new row. A task launch creates its worktrees like
`agents agterm task`. Coordinator launches are not offered.

`rename`, `type` and `screen` look the session up in a fresh tree first and answer `gone`
when it is no longer there. No method closes a session or kills a process.

## TREE

```json
{"windows": [{"id": "W", "name": "Demo", "active": true, "workspaces": [
  {"id": "WS", "name": "demo-project", "sessions": [
    {"id": "S", "name": "Topic, implementation", "status": "blocked",
     "statusChangedAt": 1700000000.0, "cwd": "/path", "unseen": 1, "flagged": false,
     "info": "Claude · RECORD", "parent": null, "companions": [], "split": false,
     "attachable": true,
     "panes": [{"pane": "left", "lead": "leader", "foreground": ["claude"]}],
     "agent": "claude",
     "record": {"id": "RECORD", "project": "demo", "task": null,
                "conversation_id": "C", "title": "demo-project"}}]}]}]}
```

- Closed windows are left out.
- `attachable` is true only when `agtermctl zmx tree` lists the session (every pane has a
  live daemon). `panes` is empty otherwise.
- `lead` is agterm's per-pane leader state (`leader` or covered); it says which client's
  size the program sees.
- `agent` comes from the registry record, else from the pane's foreground command.
- Daemon names stay on the Mac; `screen` and `attach` resolve them there.
