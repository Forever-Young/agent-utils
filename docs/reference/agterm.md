# agents agterm

Commands that run inside an agterm session: the startup chooser, launching and
resuming provider sessions, managed session records, task worktrees, worker
dispatch, restart pins and the hotkey cheatsheet. They call `agtermctl` for the
terminal side and read the project configuration from
`~/.config/agent-utils/config.json`. The project, settings and Inbox commands share
this parser but are documented as `agents admin project`, `agents admin settings`
and `agents admin inbox` in [admin.md](admin.md).

Walk-throughs: [sessions](../guide/sessions.md),
[projects and providers](../guide/projects-and-providers.md),
[tasks and workers](../guide/tasks-and-workers.md), [restore](../guide/restore.md),
[setup](../guide/setup.md).

## Global options and the shared parser

```sh
agents agterm [--config PATH] [--dry-run] COMMAND [OPERATION] [OPTIONS]
agents agterm --dry-run cheatsheet
```

| Option | Meaning |
| --- | --- |
| `--config PATH` | Configuration file. Default `$XDG_CONFIG_HOME/agent-utils/config.json`, else `~/.config/agent-utils/config.json`. Place it before the command. |
| `--dry-run` | Print the `agtermctl`, `git` or provider commands instead of running them, and save nothing. Place it before the command. Each command below says what it prints. `startup` ignores it. |

All commands use one flat argparse parser that takes `COMMAND [OPERATION]` and
every option of every command, so `agents agterm COMMAND --help` lists all options,
including ones that command ignores. This page says which options each command
reads. Errors print `Error: MESSAGE` to stderr and exit 1.

| Option | Used by | Meaning |
| --- | --- | --- |
| `--project ID` | most commands | Select the configured project by id. Filters `session list`, `dashboard`, `bootstrap`; for other `session` operations the record must belong to it. |
| `--window NAME` | launch commands, `bootstrap`, `session list`, `dashboard` | Select or filter by configured window name. |
| `--agent NAME` | launch commands, `menu`, `gui`, `history`, `session list`, `restore pin`, `ensure`, `coordinator` | Provider: `claude`, `codex`, `kiro-cli`, `opencode`, `cursor-agent`, `pi`, `agy`, `kimi`, `qwen`, `copilot`, `vibe`. Default: the project's `default_agent`, else the window's. |
| `--alternate` | `new`, `resume`, `task`, `menu`, `ensure` | Swap the provider: `claude` becomes `codex`; any other provider becomes `claude`. |
| `--session ID` | `new`, `resume`, `task`, `coordinator`, `gui` | Exact provider conversation id to resume. `ensure` refuses it. |
| `--name TITLE` | launch commands | Session title shown in agterm (and `claude --name`). Also used by `agents admin project` and `agents admin inbox`. |
| `--task ID` | `task`, `new`, `resume`, `ensure`, `session list`, `task bind`, `worktree` | Task key: branch name for worktrees, record identity, title when `--name` is absent. |
| `--description TEXT` | launch commands | Added after `--task` in the title when `--name` is absent. Default empty. |
| `--prompt TEXT` | `new`, `resume`, `task`, `coordinator`, `ensure` | First prompt given to the provider. Required by `ensure`. Kimi refuses an initial prompt. |
| `--root PATH` | `new`, `resume`, `ensure`, `menu` | Repeatable. Replaces the project's roots for this launch; the first is the starting directory. Also used by `agents admin project`. |
| `--cwd PATH` | `new`, `resume`, `task`, `menu`, `ensure` (new worker) | Starting directory, without changing roots. |
| `--repo PATH` | `worktree`, `task`, `session list`, `task bind`, `ensure` | Repeatable. Repositories for worktrees, a filter, or a declared repo link. |
| `--destination DIR` | `worktree`, `task` | Parent directory for new worktrees. Also used by `agents admin inbox move`. |
| `--base REV` | `worktree`, `task` | Base revision for new worktree branches. Default `HEAD`. |
| `--in-place` | `task` | Launch the task in the project roots without creating worktrees. |
| `--record ID` | `session` operations, `task bind`, `task rename`, `ensure` | Exact managed record id. `session` operations fall back to `$AGTERM_UTILS_RECORD`. |
| `--conversation ID` | `session bind`, `session list`, `ensure`, `restore pin` | Exact provider conversation id. |
| `--pr URL` | `session list`, `task bind`, `ensure`, launch commands | Pull request link (trailing `/` removed). |
| `--branch NAME` | `session list`, `task bind`, `ensure`, launch commands | Branch link. |
| `--alias KEY` | `task bind`, `ensure`, launch commands | Repeatable. Extra task keys for the record. |
| `--to-task KEY` | `task rename` | New task key; the old key becomes an alias. |
| `--target ID` | `restore pin` | agterm session id of the pane to pin. |
| `--compact` | `session list` | One-line compact JSON. |
| `--live` | `session list` | Only records with a verified live provider. |
| `--refresh` | `session list` | Probe liveness and save the observation. |
| `--all` | `session list`, `dashboard` | Include hidden and archived records. Also used by `agents admin inbox`. |
| `--yes` | `session forget`, `session provider-archive`, `session provider-delete` | Confirm the change. Also used by `agents admin inbox`. |
| `--backend-stopped` | `session resume`, `session provider-archive`, `session provider-delete` | Confirm the previous provider process has ended. |
| `--state STATE` | `session status` | `idle`, `running`, `active`, `blocked`, `completed` or `failed`. |
| `--result TEXT` | `session finish` | Task outcome or result-file path. |
| `--failed` | `session finish` | Save status `failed` instead of `completed`. |
| `--limit N` | `history` | Most conversations per provider, 1..1000. Default 50. |
| `--adopt ID` | `history` | Register one discovered conversation as a managed record. |
| `--provider-archived` | `history` | List Codex archived threads instead of active ones; skips Claude. |
| `--output PATH` | `cheatsheet` | File to write. |
| `--format FMT` | `cheatsheet` | `pdf`, `md` or `html`. |
| `--open` | `cheatsheet` | Open the file with `open` afterwards. |
| `--force` | `cheatsheet` | Overwrite an existing output file. Also used by `agents admin inbox`. |
| `--submenu NAME` | none | Accepts `agents`, `history` or `actions` (default `actions`). It is accepted and currently has no effect: no code reads it. |

Used only by `agents admin project|settings|inbox`, see [admin.md](admin.md):
`--launch-args`, `--agent-order`, `--auto-detect`, `--hide-agent`, `--show-agent`,
`--enabled-agent`, `--thread`, `--launch`, `--answered`, `--fix`, `--subject`,
`--message`, `--question`, `--author-host`, `--clear`, `--once`, `--interval`,
`--idle-seconds`, `--position`, `--send`, `--add-root`, `--remove-root`.

"Launch commands" below means `new`, `resume`, `task` and the launch that `menu`
ends in. A project selection that matches more than one project opens a Project
picker. Menus use Textual, fzf or a numbered list; `AGTERM_UTILS_MENU`
(`auto`, `textual`, `fzf`, `plain`) chooses, see [files.md](files.md).

## Startup and menus

### `agents agterm startup`

The chooser that the opt-in zsh hook runs on the first prompt of a new agterm
session, and that a bare `agents` runs inside agterm. Takes no options besides
`--config`; `--dry-run` is ignored.

```sh
agents agterm startup
```

1. A restored pane whose restart command was pinned by `restore sync` or
   `restore pin` skips the menu and gets a plain shell (agterm runs the pin).
2. With `new_session_position: "top"` the pane moves to the top once.
3. The project comes from the agterm workspace that owns this session (matched by
   workspace name, and by window name when names repeat). Outside a known
   workspace it comes from the current directory: the project whose root is the
   deepest match. Otherwise a Project menu opens, with **Plain shell** first.
4. The provider menu lists visible providers (installed and not hidden, or the
   manual enabled list), ordered by `agent_order` or default first, then
   **Provider
   settings** (opens the settings menu, then returns) and **Plain shell** (a shell
   in the first root).
5. For `kiro-cli`, `opencode` and `pi` on a project with several roots, a menu
   asks which single root to use for this launch.
6. The session menu starts with **New session**. Claude, Codex and OpenCode then
   list up to 100 resumable sessions of this project. Antigravity ends with
   **Continue most recent session**; every other provider except OpenCode ends
   with **Resume using provider picker**.
7. Every menu after the first ends with **← Back** to the previous step. The
   provider menu has it only when the project was picked from the Project menu.
   Escape (or Enter on an empty numbered prompt) cancels to a plain shell.

The chosen provider runs in this same pane and is registered as a managed record.
The coordinator opens in its own row; with `AGTERM_UTILS_STARTUP_HANDOFF=1` the
command then exits 10 so the hook closes the menu shell. Any error prints
`Startup menu: MESSAGE` and leaves a plain shell. See
[sessions](../guide/sessions.md).

### `agents agterm menu`

The same provider and session chooser for an explicit project, opened from any
shell. It launches in a new agterm row, not in place.

```sh
agents agterm menu --project example
```

| Option | Meaning |
| --- | --- |
| `--project ID`, `--window NAME` | Select the project; a picker opens when several match. |
| `--agent NAME`, `--alternate` | Provider listed first. |
| `--root`, `--cwd`, `--name`, `--task`, `--description`, `--prompt` | Passed to the launch. |

The provider menu here lists the default provider first and does not apply
`agent_order`, has no **← Back**, and has no single-root step: a multi-root
project fails for `kiro-cli`, `opencode` and `pi` (use `--root`). The session menu
ends with **← Back** to the provider menu. Choosing the coordinator runs
`agents agterm coordinator`.

## Launch commands

### `agents agterm new`

Start a new managed provider session in a new row of the project's workspace.

```sh
agents agterm new --project hobby
agents agterm new --project example --alternate
```

| Option | Meaning |
| --- | --- |
| `--project ID`, `--window NAME` | Select the project. |
| `--agent NAME`, `--alternate` | Provider. |
| `--root PATH`, `--cwd PATH` | Launch roots and starting directory. |
| `--name`, `--task`, `--description` | Title. `--task` (or `--alias`) already registered in this project is refused; use `ensure`. |
| `--prompt TEXT` | First prompt. |
| `--session ID` | Resume this exact conversation instead of starting one. |
| `--pr`, `--branch`, `--alias`, `--repo` | Links saved on the new record. |

Claude gets a preallocated `--session-id` UUID; other providers need
`session bind` later for exact resume. Codex launches add `--no-daemon`. The record
goes to `~/.local/state/agent-utils/registry.json` and the pane gets a restart pin.
`--dry-run` prints the `agtermctl` calls; an unknown window shows a
`<unresolved-window:NAME; allocated on apply>` marker.

### `agents agterm resume`

Like `new`, but resumes. Without `--session` it opens the provider's own picker
(`claude --resume`, `codex resume`, [Kiro](https://kiro.dev) `chat --resume-picker`, Antigravity
`--continue`); OpenCode shows this utility's picker of up to 100 project sessions.

```sh
agents agterm resume --project hobby --agent codex
agents agterm resume --project example --agent kiro-cli --session SESSION_ID
```

Options are the same as `new`.

### `agents agterm task`

Without an operation, launch a new managed session for a task in fresh worktrees:
one branch `--task` per repository, created with `git worktree add -b`.

```sh
agents agterm task --project example --task issue-123 \
  --description 'Fix parser' --prompt 'Implement issue-123 using project rules'
```

| Option | Meaning |
| --- | --- |
| `--task ID` | Required. Must pass `git check-ref-format --branch`. |
| `--repo PATH` | Repeatable. Repositories to branch; default the project roots. Each must be a repository top level. |
| `--destination DIR` | Parent of the worktrees. Default `~/.local/state/agent-utils/worktrees/HASH/TASK`. |
| `--base REV` | Base revision. Default `HEAD`. |
| `--in-place` | Skip worktrees and use the project roots. |
| other launch options | As for `new`; `--root` is ignored. |

Refuses a dirty repository, an existing branch, an existing target or colliding
repository basenames. A failure after some worktrees were created leaves them in
place. `--dry-run` prints the `git` and `agtermctl` commands. See
[tasks and workers](../guide/tasks-and-workers.md).

### `agents agterm task bind`

Add links to one existing worker record and print it as compact JSON.

```sh
agents agterm task bind --record RECORD_ID --pr https://example.invalid/team/app/pull/42 \
  --branch feature/parser --alias provisional-parser --repo ~/Projects/example
```

| Option | Meaning |
| --- | --- |
| `--record ID` | Worker to change. Without it, `--project`, `--window`, `--agent`, `--task`, `--conversation` select exactly one worker. |
| `--task KEY` | With `--record`: set the task key, or add it as an alias when the record already has another. |
| `--pr`, `--branch`, `--alias`, `--repo` | Links to add (`--alias` and `--repo` repeat). They are not filters here. |

Refuses when the selection matches zero or several workers, or when a task key
already belongs to another worker of the project. `--dry-run` prints the result
without saving.

### `agents agterm task rename`

Give a worker a new task key; the old key stays as an alias.

```sh
agents agterm task rename --record RECORD_ID --to-task ISSUE-123
```

| Option | Meaning |
| --- | --- |
| `--record ID` | Worker to rename (or select it as for `task bind`). |
| `--to-task KEY` | Required. New task key. |
| `--alias`, `--pr`, `--branch`, `--repo` | Links added at the same time. |

### `agents agterm ensure`

Send a request to the one worker for an identity: reuse it when live, resume its
conversation when no live writer exists, or create it. Claude only.

```sh
agents agterm ensure --project example --window Work --agent claude \
  --task ISSUE-123 --prompt 'Address the review comments'
```

| Option | Meaning |
| --- | --- |
| `--project ID`, `--window NAME` | Must select exactly one project. |
| `--task`, `--pr`, `--branch`, `--record`, `--conversation` | Identity; at least one is required. `--alias` values also match. |
| `--prompt TEXT` | Required. The request. |
| `--agent claude` | Any other provider is refused. |
| `--alias`, `--pr`, `--branch`, `--repo` | Links added to the worker. |
| `--root`, `--cwd`, `--name`, `--description` | Used only when a new worker is created. |

Prints one JSON line with `action` (`reused`, `resumed`, `created`; `blocked` under
`--dry-run`), the record, conversation and terminal ids, `state`, `alive` and
`delivery`. A live worker gets one typed line pointing to a message file in
`~/.local/state/agent-utils/messages/`; `delivery: submitted` means the line was
typed, not that the worker handled it. Unknown liveness, a blocked or detached
worker, several matches or an unavailable `claude agents --json` inventory fail
instead of creating a second worker. See
[tasks and workers](../guide/tasks-and-workers.md).

### `agents agterm coordinator`

Open the project's coordinator (**Main**; Claude, Codex or Kiro, see [the guide](../guide/tasks-and-workers.md#the-coordinator)): focus its row when
open, otherwise resume its recorded conversation, otherwise create it. It runs with
Sonnet, the dispatcher prompt and a PreToolUse tool gate.

```sh
agents agterm coordinator --project example
```

| Option | Meaning |
| --- | --- |
| `--project ID`, `--window NAME` | Select the project. |
| `--agent claude` | Any other provider is refused. |
| `--session ID` | On first use, adopt this Claude conversation instead of a new UUID. |
| `--prompt TEXT` | First prompt. |

Refuses when the recorded conversation runs outside its recorded row. The record is
in `registry.json` under `coordinators`.

### `agents agterm gui`

Hand the project to the provider's desktop app. Claude and Codex only.

```sh
agents agterm gui --project example --agent codex
```

| Option | Meaning |
| --- | --- |
| `--project ID`, `--window NAME` | Select the project. |
| `--agent NAME` | `claude` runs `claude --desktop`; `codex` runs `codex app ROOT`. Others are refused. |
| `--session ID` | Claude only: add `--resume ID`. |

Runs in the first root and exits with the app command's exit code. `--dry-run`
prints the command.

## Projects and configuration

### `agents agterm list`

Print the enabled projects as a JSON array, each with its `window` name.

```sh
agents agterm list
```

### `agents agterm validate`

Load and check the configuration; prints `Configuration valid`, or an error and
exit 1.

```sh
agents agterm --config ~/Projects/example/config.json validate
```

### `agents agterm doctor`

Print JSON with the path found for `agtermctl`, each provider and `fzf` (`null` when
missing), and the configuration path. Does not read the configuration.

```sh
agents agterm doctor
```

### `agents agterm bootstrap`

Create missing agterm windows and workspaces for enabled projects. Starts no
provider. With `new_session_position: "top"` it first records the existing rows so
they are never moved.

```sh
agents agterm bootstrap
agents agterm --dry-run bootstrap --window Work
```

| Option | Meaning |
| --- | --- |
| `--window NAME` | Only this window. |
| `--project ID` | Only this project. |

### `agents agterm worktree`

Create one `git worktree` per repository on a new branch, without launching
anything.

```sh
agents agterm worktree --project example --task issue-123 \
  --destination ~/Projects/worktrees --repo ~/Projects/example
```

| Option | Meaning |
| --- | --- |
| `--task NAME` | Required. Branch name: letters, digits, `-`, `_`, `/`, not starting with `-`. |
| `--destination DIR` | Required. Each worktree goes to `DIR/REPO_BASENAME`. |
| `--repo PATH` | Required, repeatable. |
| `--base REV` | Base revision. Default `HEAD`. |
| `--project`, `--window` | A project is still selected (picker when ambiguous) but not used. |

Refuses dirty repositories, colliding basenames, an existing target and an unknown
base. `--dry-run` prints the `git` commands.

## Sessions

Managed records live in `~/.local/state/agent-utils/registry.json`. Hide, archive
and forget change only these records, never terminals or provider history. Every
operation except `list` needs `--record ID` (or `$AGTERM_UTILS_RECORD`), and
`--project`, when given, must match the record. Under `--dry-run` the changing
operations print the updated record instead of saving it. See
[sessions](../guide/sessions.md).

### `agents agterm session`

Without an operation, the same as `session list`.

### `agents agterm session list`

Print managed records as JSON.

```sh
agents agterm session list --project example --task ISSUE-123
```

| Option | Meaning |
| --- | --- |
| `--project`, `--window`, `--agent` | Filters. |
| `--task`, `--conversation`, `--pr`, `--branch`, `--repo`, `--record` | Identity filters (AND; `--task` matches aliases). Any of them includes hidden and archived records, refreshes liveness and prints compact output. |
| `--all` | Include hidden and archived records. |
| `--compact` | Compact one-line output. |
| `--refresh` | Probe liveness and save `observed_state`, `checked_at`, `last_seen`. |
| `--live` | Only records with `alive: true` (implies probing). |

`state` is the fresh observation (`running`, `idle`, `blocked`, `dead`, `unknown`,
or `completed`/`failed` after exit); `alive: null` means unknown.

### `agents agterm session focus`

Select the record's open row. Fails when the row is closed; use `session resume`.

```sh
agents agterm session focus --record RECORD_ID
```

### `agents agterm session resume`

Focus the record's row when open. Otherwise resume its bound conversation in a new
row with its saved roots, directory and window.

```sh
agents agterm session resume --record RECORD_ID --backend-stopped
```

| Option | Meaning |
| --- | --- |
| `--record ID` | Record to resume. |
| `--backend-stopped` | Required when the row is closed: confirms the old provider process ended. |

Fails without a bound conversation id. `--dry-run` prints what it would do.

### `agents agterm session hide`

Set `hidden` on the record so utility menus and listings skip it. The next
`restore sync` clears the pane's restart pin.

```sh
agents agterm session hide --record RECORD_ID
```

### `agents agterm session unhide`

Clear `hidden`.

```sh
agents agterm session unhide --record RECORD_ID
```

### `agents agterm session archive`

Set `archived` on the record. Like `hide`, the next `restore sync` clears its pin.

```sh
agents agterm session archive --record RECORD_ID
```

### `agents agterm session unarchive`

Clear `archived`.

```sh
agents agterm session unarchive --record RECORD_ID
```

### `agents agterm session forget`

Delete the utility record. Provider history stays.

```sh
agents agterm session forget --record RECORD_ID --yes
```

| Option | Meaning |
| --- | --- |
| `--yes` | Required. |

### `agents agterm session bind`

Record the provider conversation id, then update the pane's restart pin.

```sh
agents agterm session bind --record RECORD_ID --conversation ACTUAL_UUID
```

| Option | Meaning |
| --- | --- |
| `--conversation ID` | Required. A UUID for Claude and Codex; for other providers letters, digits, `_`, `-`, up to 256 characters. |

Refuses to replace a different id that is already bound.

### `agents agterm session status`

Save a lifecycle status and set the agterm row status (`running` shows as
`active`, `failed` as `blocked`).

```sh
agents agterm session status --record RECORD_ID --state blocked
```

| Option | Meaning |
| --- | --- |
| `--state STATE` | Required: `idle`, `running`, `active`, `blocked`, `completed` or `failed`. |

### `agents agterm session finish`

Save the task result with status `completed` (or `failed`), set the row status and
send an agterm notification to the row and to the project's open coordinator.

```sh
agents agterm session finish --record RECORD_ID --result 'Implemented; tests passed'
```

| Option | Meaning |
| --- | --- |
| `--result TEXT` | Required. Outcome or result-file path. |
| `--failed` | Save status `failed`. |

The result is saved even when the row is closed.

### `agents agterm session provider-archive`

Run `codex archive UUID` for the record's conversation. Codex only.

```sh
agents agterm session provider-archive --record RECORD_ID --yes --backend-stopped
```

| Option | Meaning |
| --- | --- |
| `--yes`, `--backend-stopped` | Both required. |

Needs a bound conversation id and refuses while the recorded row is open.
`--dry-run` prints the `codex` command.

### `agents agterm session provider-unarchive`

Run `codex unarchive UUID`. Codex only; needs a bound id; no confirmation flags.

```sh
agents agterm session provider-unarchive --record RECORD_ID
```

### `agents agterm session provider-delete`

Run `codex delete UUID --force` and mark the record deleted and archived. Codex
only. Same requirements as `provider-archive`.

```sh
agents agterm session provider-delete --record RECORD_ID --yes --backend-stopped
```

### `agents agterm history`

List provider conversations whose directory is one of the project's roots (and the
roots of its records): Claude from bounded metadata headers of local JSONL files,
Codex through `codex app-server`. Prints JSON.

```sh
agents agterm history --project example --agent codex
agents agterm history --project example --agent claude --adopt ACTUAL_UUID
```

| Option | Meaning |
| --- | --- |
| `--project ID`, `--window NAME` | Select the project. |
| `--agent claude\|codex` | Only this provider; others are refused. Default both. |
| `--limit N` | 1..1000 per provider. Default 50. |
| `--provider-archived` | Codex archived threads; Claude is skipped. |
| `--adopt ID` | Register this discovered conversation as a record (status `imported`) and print it. Prints `Already managed: ID` when a record exists. |

`--dry-run` without `--adopt` prints a message only; with `--adopt` it prints the
record without saving.

### `agents agterm dashboard`

Interactive snapshot of open rows and closed records, blocked and completed first.
Actions: focus, show details, hide/unhide, archive/unarchive, resume (asks to
confirm the old process ended). **Refresh** takes a new snapshot.

```sh
agents agterm dashboard --window Work
```

| Option | Meaning |
| --- | --- |
| `--window NAME`, `--project ID` | Filters. |
| `--all` | Start with hidden and archived records shown. |

`--dry-run` prints a message only.

## Restore

Restart pins are per-pane resume commands stored with `agtermctl session restore`.
See [restore](../guide/restore.md).

### `agents agterm restore`

Requires an operation: `sync`, `snapshot`, `plan` or `pin`.

### `agents agterm restore sync`

Bind conversation ids of live Codex and Kiro panes that have none (from the open
rollout or lease file, only when one candidate matches), register panes where
Kiro or Codex was started by hand (project `manual`), then set, update or clear
each managed pane's restart pin. Pins you set yourself are kept. Prints one line
per action and appends a line to `~/.local/state/agent-utils/restore-sync.log`
(last 200 kept).

```sh
agents agterm restore sync
```

`--dry-run` prints a message only.

### `agents agterm restore snapshot`

Write `~/.local/state/agent-utils/restore-snapshot.json` with the open rows and
their records. The previous file becomes a `.bak`; 3 backups are kept.

```sh
agents agterm restore snapshot
```

### `agents agterm restore plan`

Compare the last snapshot with the open rows and print `missing`, `renamed`,
`unpinned` and `new` lines. Read-only.

```sh
agents agterm restore plan
```

### `agents agterm restore pin`

Pin one open pane to a provider conversation, registering the pane when it has no
record. Asks for whatever the options do not give; for Kiro without an id it lists
up to 15 recent conversations of the pane's directory.

```sh
agents agterm restore pin --target SESSION_ID --agent codex --conversation ACTUAL_UUID
```

| Option | Meaning |
| --- | --- |
| `--target ID` | agterm session id; default a picker of open panes. |
| `--agent NAME` | Default the record's provider, else a picker. |
| `--conversation ID` | Default the record's id; validated as for `session bind`. |

`--dry-run` prints the pin line without saving.

## Other

### `agents agterm cheatsheet`

Write a hotkey cheatsheet from `agtermctl keymap list` and `keymap.conf`.

```sh
agents agterm cheatsheet --open
agents agterm cheatsheet --format md --output ~/keys.md --force
```

| Option | Meaning |
| --- | --- |
| `--format FMT` | `pdf` (needs reportlab in the runtime; a source install adds it with `--with-pdf`), `md` or `html`. Default `pdf` when reportlab is present, else `html`. |
| `--output PATH` | Default `~/.local/state/agent-utils/cheatsheets/agterm-hotkeys.FMT`. |
| `--force` | Overwrite an existing file; without it an existing file is an error. |
| `--open` | Open the file afterwards. |

`--dry-run` prints the target path. The file reflects a personal keymap; do not
commit it.
