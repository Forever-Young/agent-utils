# agents zellij

Open, find and focus project agent sessions as Zellij tabs, locally or over SSH. Code:
`agent_utils/core/zellij.py`. Needs Zellij 0.46 or newer (tab IDs, `--no-focus`, `list-tabs`);
`open` checks `zellij action --help` first and says which command is missing.
Unit tests use a fake `zellij`.

```sh
agents zellij [COMMAND] [OPTIONS]
```

Inside a Zellij pane a bare `agents` runs `agents zellij menu` (see the context rule in the
[index](README.md)). From outside Zellij pass `--session NAME` for a session that has a client attached.

| Option | Meaning |
|---|---|
| `COMMAND` | `run`, `launcher`, `menu`, `open`, `list`, `focus`, `close`, `adopt`, `layout` or `doctor`. |
| `TARGET` | For `focus` and `close`: a record ID (or its prefix), a task key or a tab ID. For `layout`: `list`, `show`, `new` or `set`. |
| `NAME` | For `layout show` and `layout new`: the layout name. |
| `--config PATH` | Config file with the projects. Default: `~/.config/agent-utils/config.json`. |
| `--session NAME` | Zellij session. Default: current (`ZELLIJ_SESSION_NAME`), or `zellij.session` for `run` outside Zellij. |
| `--project ID` | `open`: project ID or name. |
| `--agent NAME` | `open`: provider. Default: the project's default. |
| `--task KEY` | `open`: task key. A live tab for the same task is focused instead of opening a second one. |
| `--resume` | `open`: resume with the provider's picker, or the conversation from `--conversation`. |
| `--conversation ID` | `open`: exact provider conversation ID to resume. |
| `--no-focus` | `open`: create the tab without switching to it. |
| `--layout NAME` | `open`: use this layout instead of the project's `zellij.layout`. |
| `--from PRESET` | `layout new`: preset to copy. Default `agent-shell`. |
| `--self` | `adopt`: adopt the agent this command runs under (exact; for use inside the pane or from a hook). |
| `--pane ID` | `adopt`: adopt the agent in this terminal pane (bare integer from `list-panes`). |
| `--all` | `adopt`: every unambiguous unmanaged agent pane. `list`: every active session agents created, plus the current one. |
| `--rename` | `adopt`: rename the tab to `Project: agent`. |
| `--quiet` | `adopt --self`: print nothing and exit 0 (hooks). |
| `--force` | `close`: also close a tab whose provider process is still running. |
| `--json` | `list`: machine-readable rows. |
| `--resurrect` | `run`: bring back an exited session and start its tabs again. |
| `--fresh` | `run`: kill and delete the session, then start it from scratch. |
| `--dry-run` | Print the `zellij` commands that would change anything. Read-only queries still run. |

## Commands

### `agents zellij run`

From a plain SSH shell, `agents zellij` defaults to `run`: start or attach to the
configured session, default `agents`. Use `agents zellij run --session mobile` to
choose another name. Existing live sessions are attached without adding or replacing
tabs, and keep the configuration they were started with: the Zellij config file
and layout apply when a session is created, so to pick up a changed
`zellij.config_path` or `ssh_config_path`, end the session and run again. An exited
session is refused with the exact command to bring it back: `agents zellij run
--resurrect` (add `--session NAME`) attaches it and starts its tabs again; without
that flag Zellij would leave every restored pane suspended until ENTER. To discard the
session instead, `agents zellij run --fresh` kills and deletes it (live or exited, so
running agents in it stop) and starts a new one with the launcher tab. Inside Zellij, bare `agents
zellij` still defaults to `menu`; `run` in the current session opens the tab chooser.

A new session starts with a chooser tab. Every new tab (Ctrl+t, then n with default
keys) uses the same chooser: **Choose project and agent** or **Shell**. The agent
choice opens the existing project/provider/new/resume flow in a managed agent tab.
The chooser tab becomes a shell afterwards, also on cancellation. Shell means an
ordinary login shell inside Zellij, so you can run `agents` again. Use Ctrl+o, then d
to detach; rerun `agents zellij` after reconnecting. A disconnect leaves the server
and its processes running. No existing SSH process is migrated into Zellij.

`agents zellij launcher` opens the same chooser in the current pane. It is also the
entry point used by generated tab layouts. `--dry-run` prints commands and never
attaches. `run` from inside Zellij cannot switch to another session; use its session
manager instead. Agent tabs retain the window/project layout and session settings.
Managed agent tabs use an explicit pane layout even when no project layout is set,
so the workspace's chooser template cannot replace the selected agent command.
This also works in launcher workspaces created before this fix; no restart is needed.

### Mobile profile and config paths

In `agents configure`, open **Projects and windows** and choose **Install the
contributed mobile Zellij config**. Or preview/apply it from the command line:

```sh
agents admin config run zellij-mobile-install
agents admin config run zellij-mobile-install --yes
```

Source: `agent_utils/contrib/zellij/mobile.kdl`, shipped with the installed package.
It disables mouse capture and pane frames, uses simple font symbols and a compact
layout, and sets `on_force_close "detach"`. No desktop config is changed.
Installation is opt-in, atomic and refuses customized files or symlinks. To remove
an unchanged copy use `agents admin config run zellij-mobile-remove --yes`; edited
copies are preserved. The same installer is the `zellij-mobile` item under optional
configuration. Module: `python -m agent_utils.core.setup.zellij_mobile`, with
`--home PATH`, `--apply` and `--remove` (preview by default).

These editable settings appear in `agents configure` and `agents admin config`:

| Key | Default | Meaning |
|---|---|---|
| `zellij.config_path` | empty | Zellij UI config for local clients; empty uses Zellij's default. |
| `zellij.ssh_config_path` | `~/.config/zellij/config-ssh.kdl` | UI config for SSH clients, and mobile profile install destination. |
| `zellij.session` | `agents` | Default session name for `run` outside Zellij. |

All three live under the top-level `zellij` object in the agents project config.
The SSH path is used automatically on create and attach. An absent default file
falls back to Zellij's config; an explicitly configured missing file is an error.
An empty SSH path uses Zellij's default and disables mobile profile installation.
The installer reads `~/.config/agent-utils/config.json` under `--home`; it does not
read a command-specific alternate project registry. Set the destination before
installing. Example changes (preview without `--yes`):

```sh
agents admin config set zellij.ssh_config_path ~/phone.kdl --yes
agents admin config set zellij.session mobile --yes
```

`agents zellij --config PATH` selects the **agents project registry**, not the Zellij
UI config. The config path settings choose the UI config. No shell startup hook is
installed. A running session's existing tabs and their layout are preserved on
reattach; the chooser template is installed only when `run` creates a new session.

### `agents zellij menu`

The flow for a shell inside Zellij or an SSH shell. Always pick a window/project,
even when the current directory belongs to a configured project. If the
project already has live tabs, attach to one (focus it) or continue. Then the agent menu, the
single-root choice for agents that need it, and new / history / provider picker, as in `agents agterm
startup`. The result is a tab named `Project: agent-or-task` in the project root. Outside a Zellij
session it creates the configured shared or project session when needed and attaches
the client to it. With the default `shared` setting and no current session,
it uses a session named `agents`. An exited destination must be resurrected first.

### `agents zellij open`

`agents zellij open --project ID [--agent A] [--task KEY] [--resume] [--conversation ID]`.
Builds the same managed record and runner command as `agents agterm new`, runs
`zellij action new-tab --name ... --cwd ROOT -- COMMAND`, and verifies the tab exists (Zellij prints an ID
even when the session has no attached client; that case fails with a message). It then sets the pane's
resurrect command to the runner command and stores `zellij: {session, tab_id, pane_id, resumable}` in the record.

### `agents zellij list`

All tabs of the session with attention state (`needs you`, `working`, `done`, ...), joined to registry
records, plus records whose tab is gone. Join is by tab ID; names are display only.

### `agents zellij focus`

`agents zellij focus TARGET`: `go-to-tab-by-id`.

### `agents zellij close`

`agents zellij close TARGET`: `close-tab-by-id`. Refuses while the recorded provider process is alive unless `--force`.

### `agents zellij doctor`

Zellij version, whether this shell is in Zellij, the session, and each `zellij action` command this
integration needs (`ok` or `MISSING`). Exit 1 if anything is missing.

### `agents zellij adopt`

Makes an agent you started by hand in a pane (typed `claude`, `kiro-cli chat`, `codex`) a managed session:
it shows in `list`, `focus` works, and it **resumes after a Zellij restart**, because its pane gets the runner
plus the resume command as its resurrect command. Without options it lists unmanaged agent panes.

- `agents zellij adopt --self` (inside the pane, e.g. `!agents zellij adopt --self` in the agent, or the opt-in
  SessionStart hook from [`configure-zellij-status.py --adopt`](setup.md#agent_utilscoresetupzellij_status)) is exact:
  the pane is `ZELLIJ_PANE_ID` and the agent is the nearest ancestor process whose executable is a known agent.
- `agents zellij adopt --pane ID` or `--all` from another pane matches the pane's foreground command and folder
  to unmanaged agent processes under a Zellij server. It adopts only when exactly one matches; otherwise it names
  the candidates and suggests `--self`.

The conversation ID comes from the hook's `session_id`, the agent's environment (`KIRO_SESSION_ID`,
`CODEX_THREAD_ID`, ...), the command line (`--resume ID`, `--session-id ID`, `codex resume ID`), or, for [Kiro](https://kiro.dev) and
Codex, the restore discovery by process. Without one the record is still made, but the tab does not resume.
The project comes from the agent's folder (`manual` when no project root contains it). Nothing is typed into
panes and no transcript is changed. A pane, process or conversation that already has a record is refused.
An adopted process is not wrapped until the next restart: its exit is noticed by the alive check, not the runner.
"Already managed" is decided by the process tree (the runner is the provider's parent), not by
`AGTERM_UTILS_RECORD`: a Zellij server passes the environment it was started with to every pane.

### `agents zellij layout`

`agents zellij layout list` shows presets and your own files. `agents zellij layout show NAME [--project ID]`
prints the KDL that `open` would use. `agents zellij layout new NAME [--from PRESET]` copies a preset to
`~/.config/agent-utils/zellij-layouts/NAME.kdl` to edit; it never overwrites. `agents zellij layout set` is a
menu: choose a window or project, then its layout and session; it writes `config.json` with a backup. The same
menu is in `agents configure` (Projects, "Choose a Zellij layout and session"), and
`agents admin settings --project P --zellij-layout L --zellij-session S` sets it without a menu.

| Preset | Arrangement |
|---|---|
| `agent` | the agent only (the default without a layout) |
| `agent-shell` | agent left, shell right (30 %) |
| `agent-shell-below` | agent top, shell below (30 %), for narrow screens |
| `agent-shell-logs` | agent left; shell and a `logs` pane right, running `zellij.logs_command` (suspended until ENTER) |

A layout file is the body of one tab. It must contain the line `{{agent}}` exactly once; that line becomes the
agent pane (named `agent`, running the runner command). `{{root}}`, `{{project}}` and `{{task}}` insert text for
use inside quotes; `{{logs}}` inserts the logs pane. The tab is wrapped with the session's own `new_tab_template`,
so it keeps the tab and status bars. Only the agent pane gets the resurrect command; shells come back as shells.

```kdl
// ~/.config/agent-utils/zellij-layouts/web.kdl
pane split_direction="vertical" {
    {{agent}}
    pane size="35%" split_direction="horizontal" {
        pane name="shell"
        pane name="dev" command="npm" start_suspended=true {
            args "run" "dev"
        }
    }
}
```

## Project settings

Optional `zellij` object on a project, or on a window as the default for its projects:

```json
{"id": "demo", "name": "Demo", "roots": ["~/src/demo"],
 "zellij": {"layout": "agent-shell", "session": "project", "logs_command": ["tail", "-f", "log/dev.log"]}}
```

| Key | Meaning |
|---|---|
| `layout` | Layout name (preset or own file). Absent: a single agent pane. |
| `session` | `shared` (default): tabs go into the current session. `project`: the project's own session `agents-<project id>`. Any other name: that session (several projects can share it). |
| `logs_command` | Command for the `{{logs}}` pane. |

### One session per project

With `"session": "project"`, `open` and the menu put the project's tabs in `agents-<project id>`:

- Missing session: created in the background with the first agent tab already in it
  (`zellij --layout-string ... attach --create-background NAME`). No client is needed. Agterm and Zellij
  variables are not passed to the new server. The session is recorded as created by agents.
- Active session with a client: a new tab in it. Without a client Zellij cannot add tabs; `open` says so.
- A session of that name that agents did not create is refused (set its name explicitly to use it). An exited
  session must be resurrected first (`zellij attach NAME`).
- Getting there: from a plain or SSH shell the menu ends with `exec zellij attach NAME` (with
  `~/.config/zellij/config-ssh.kdl` over SSH when present). Inside Zellij the menu and `open` (without
  `--no-focus`) run `zellij action switch-session NAME --tab-position N`, which moves the client that last typed
  in the current session (seen live on 0.46.0), and check that a client arrived. If not, they print the session
  manager hint (Ctrl o w).
- Nothing here kills or deletes sessions.

Zellij's cross-session inbox and `go-to-next-attention` (Ctrl o n) show agents that need you in any project
session. `agents zellij list --all` lists the tabs of every session agents created.

## Restart and resume

After a Zellij restart, panes come back suspended with the resurrect command (the runner). Press ENTER in a
tab and the runner resumes the recorded conversation. The runner rewrites the record's `zellij` field
every start, because resurrection renumbers tab and pane IDs. Nothing starts without ENTER. Tabs are matched to records by the record ID in the pane's own command line, so
renumbered IDs do not mix up records; the stored tab ID is only a fallback.

Caveat (seen live): `zellij kill-session` can leave the old provider processes running. Pressing ENTER then starts
a second process on the same conversation. Quit sessions with Zellij's own quit, or stop the old process first.

## Tab status

Tab markers come from Zellij's own attention tracking. Agents report state with the escape sequence
`OSC 7501` written to the pane's terminal (`agent_utils/core/zellij.py: emit_status`):

| Agent event | Tab shows |
|---|---|
| prompt submitted, tool finished | working |
| permission request, notification | needs you |
| turn finished | done |

Kiro: the existing hook file ([`configure-kiro.py`](setup.md)) also reports to Zellij. Claude, Qwen, Codex, Pi and OpenCode:
[`configure-zellij-status.py`](setup.md#agent_utilscoresetupzellij_status). `agents agent rename`
also renames the tab (keeping a `Project: ` prefix). All of it is a no-op outside Zellij.

