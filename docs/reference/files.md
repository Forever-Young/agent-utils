# Files and environment variables

Every file outside the repository that agent-utils reads or writes, and every
environment variable it reads. Paths and the one-time migration come from
`agent_utils/core/paths.py`. Walk-through for the install side: [setup guide](../guide/setup.md).

## Folders

| Folder | Default | Override |
|---|---|---|
| Config dir | `~/.config/agent-utils` | `XDG_CONFIG_HOME` (then `$XDG_CONFIG_HOME/agent-utils`) |
| State dir | `~/.local/state/agent-utils` | `XDG_STATE_HOME` |
| Data dir | `~/.local/share/agent-utils` | `XDG_DATA_HOME` |
| Install dir | `~/.bin` and `~/.bin/agterm-tools` | `scripts/install.py --bin-dir` |

**Migration from agterm-utils.** The project used to be called agterm-utils. When
`agent-utils` does not exist in the config or state base folder and `agterm-utils` does,
the first access copies the old folder to the new name (symlinks kept). The old folder
is left untouched as a backup; you can delete it once the new one works. Files named
`agterm-utils-*` (the [Kiro](https://kiro.dev) hook file, zsh block markers) keep the old spelling so
installed hooks keep working.

"Safe to delete" means agent-utils keeps working; the column says what you lose.

## Config dir

| Path | Written by | Purpose | Safe to delete? |
|---|---|---|---|
| `config.json` | you; `agents admin project ...`, `agents admin config set/unset/undo` | Windows, projects, providers, `remotes`, `new_session_position`, per-project `zellij`. | No: it holds your projects. |
| `zellij-layouts/NAME.kdl` | you; `agents zellij layout new` | Your Zellij tab layouts. | No: they are yours. |
| `config.json.backup-STAMP` | the commands above, before each change | Previous versions; `agents admin config undo config` restores the newest. | Yes. |
| `config.json.lock` | the commands above | Write lock. | Yes, when no command runs. |
| `notify.json` | `agents admin notify ...`, `agents admin config set` | Push notification switch, ntfy URL, templates, the idle time before a push (`away_after_seconds`) and the locked-screen switch (`away_when_locked`). Absent = off. | Yes: notifications turn off. |
| `snippets.json` | you; `agents admin config set` | Variables for instruction snippets. | Yes: snippets lose their variable values. |
| `skills.json` | `agents admin skills ...` | Skills link policy per agent. Backups `skills.json.backup-STAMP` beside it. | Yes: the default policy applies. |
| `instructions/` | you; `agents admin instructions compose` | Shared rule sources: `common.md` and per-agent parts such as `codex.md`. | No while `agents admin instructions apply` targets link to it. |
| `snippets/` | you | Your own instruction snippets, next to the bundled library. | Yes, if you do not use them. |

## State dir

| Path | Written by | Purpose | Safe to delete? |
|---|---|---|---|
| `registry.json`, `registry.lock` | session, task and coordinator commands; `agents zellij` | Managed sessions, coordinators, workers, results; Zellij sessions agents created (`zellij_sessions`). | No: managed sessions lose their records and resume commands. |
| `placement.json`, `placement.lock` | new-row placement | Rows already moved to the top, and the bootstrap baseline. | Yes: existing rows may be placed again once. |
| `inbox.json`, `inbox.lock` | `agents admin inbox dispatch/adopt` | Which Inbox threads were dispatched to which record. | Yes: threads may be dispatched again. |
| `inbox-relay.json`, `inbox-relay.json.lock` | Kiro hooks, `agents admin inbox relay` | Live Kiro sessions and their connected Inbox threads. | Yes: thread connections are lost. |
| `inbox-relay.log` | the relay launchd job | Relay output. | Yes. |
| `dispatch.lock` | worker dispatch | Lock. | Yes, when no command runs. |
| `messages/RECORD/ID.json` | worker follow-ups | Messages sent to a worker. | Yes, after the work is done. |
| `worktrees/HASH/TASK/` | `agents agterm task ...` | Automatic task worktrees (git worktrees); `HASH` is 12 hex characters derived from the project id. | No while the task runs; remove finished ones with `git worktree remove`. |
| `restore-snapshot.json` (+ 3 `.STAMP.bak`) | `agents agterm restore sync/snapshot` | Snapshot for reopening agterm into the same state. | Yes: the next restore has no snapshot. |
| `restore-sync.log` | `agents agterm restore sync` | Sync log. | Yes. |
| `ask/RUN/` | `agents agent ask` | `prompt.md`, `TARGET.md` answers, `TARGET.log` stderr. | Yes. |
| `audits/TAG/STAMP/` | `agents admin skills audit/extract` | Reports and patches. | Yes. |
| `backups/STAMP/` | `agents admin skills ...`, `agents admin skills prune-rules` | Copies of skills and rule files before a change. | Yes, once you do not need to undo. |
| `cheatsheets/agterm-hotkeys.FORMAT` | `agents agterm cheatsheet` | Generated hotkeys sheet (html or pdf). | Yes. |
| `session-names/pending/`, `seen/`, `named/` | `agents agent rename`, session-name hooks | Names waiting for the next prompt, last copied names, reminder counters. | Yes: reminders start again. |
| `skills-check.json`, `skills-check.lock`, `skills-check.log` | `agents admin skills check` and its schedule | Drift-check state, lock, scheduled log. | Yes: the next check starts fresh. |

## Other locations

| Path | Written by | Purpose | Safe to delete? |
|---|---|---|---|
| `~/.local/share/agent-utils/skills/` | `agents admin skills` marketplace imports | Imported skills that agent folders link to. | No while links point to it. |
| `~/.agents/skills/` | `agents admin skills ...` | The shared skills store. | No while links point to it. |
| `~/.bin/agents` | `scripts/install.py --apply` | The command. | Yes: uninstalls the command. |
| `~/.bin/agterm-tools/agent_utils/` | `scripts/install.py --apply` | Installed package; hooks run from here. | No while hooks are configured. |
| `~/.bin/agterm-tools/agterm_utils/` | `scripts/install.py --apply` | Forwarders for hooks written before the rename. | Yes, once no hook names the old path. |
| `~/.bin/agterm-tools/runtime/` | `scripts/install.py --with-tui` or `--with-pdf` | Isolated venv with Textual and reportlab. | Yes: menus fall back to fzf or numbered lists. |
| `~/.bin/agterm-tools/backups/STAMP/` | `scripts/install.py --apply` | Code snapshots for `--rollback`. | Yes, except those you may roll back to. |
| `~/.bin/agterm-tools/install-manifest.json` | `scripts/install.py --apply` | Install time, backup, source checkout. | Yes: install actions from `agents` then need `--source`. |
| `~/.bin/agterm-tools/agterm-startup.zsh` | `scripts/configure-zsh.py --apply` | zsh startup hook sourced by `~/.zshrc`. | No while the `~/.zshrc` block exists. |
| `~/.cache/agent-utils/brew/` | `scripts/brew-local.py --apply` | Tarballs for the local formula. | Yes. |
| `~/Library/LaunchAgents/agent-utils.inbox-relay.plist` | `scripts/configure-relay.py --apply` | Keeps the Inbox relay running. | Use `--remove --apply`. |
| `~/Library/LaunchAgents/agent-utils.skills-check.plist` | `agents admin skills schedule install --apply` | Scheduled skills check. | Use `agents admin skills schedule remove --apply`. |

### Provider and shell files, opt-in only

agent-utils edits these only through the named command, backs each file up beside
itself first (`FILE.backup-STAMP`), and changes only its own entries.

| Path | Command | Undo |
|---|---|---|
| `~/.kiro/hooks/agterm-utils-attention.json` | `scripts/configure-kiro.py --apply` | `--apply --remove` |
| `~/.claude/settings.json`, `~/.qwen/settings.json`, `~/.codex/hooks.json` (entries running `zellij_status.py`), `~/.kiro/hooks/agent-utils-zellij-adopt.json` (with `--adopt`) | `scripts/configure-zellij-status.py --apply` | `--apply --remove` |
| `~/.claude/settings.json`, `~/.qwen/settings.json`, `~/.codex/hooks.json`, `~/.kiro/hooks/agent-utils-session-names.json`, `~/.pi/agent/extensions/agent-utils-session-name.ts` | `scripts/configure-session-names.py --apply` | `--apply --remove` |
| `~/.zshrc` | `scripts/configure-zsh.py --apply` | delete the managed block or restore the backup |
| `~/.config/agterm/ghostty.conf` | `scripts/configure-startup.py --apply` | delete the managed block or restore the backup |
| Agent rule files (`~/.claude/rules/agent-utils*.md`, `~/.kiro/steering/agent-utils*.md`, a marked block in `~/.codex/AGENTS.md` and `~/.pi/agent/AGENTS.md`, `~/.config/opencode/opencode.json`, `~/.cursor/rules/agent-utils.mdc`, others) | `agents admin instructions apply --yes` | `agents admin instructions remove --yes` |
| Agent skill folders (`~/.claude/skills`, `~/.kiro/skills`, ...) | `agents admin skills sync/link --apply` | see [admin.md](admin.md) |
| A project's rule files | `agents admin skills prune-rules PROJECT --apply` | copies in `backups/` |

## Environment variables

### Variables you can set

| Variable | Read by | Meaning |
|---|---|---|
| `AGTERM_UTILS_MENU` | every picker (`agent_utils/cli.py`) | Menu backend: `auto` (default), `textual`, `fzf` or `plain` (numbered). |
| `AGTERM_UTILS_ZELLIJ` | `agents zellij` (`core/zellij.py`) | Path of the `zellij` binary to use. Default: `zellij` on PATH. |
| `AGTERM_UTILS_LAUNCHER` | the zsh startup hook | Path of the `agents` command to run at the first prompt. Default `~/.bin/agents`, else `agents` on PATH. |
| `AGTERM_UTILS_PLAIN_SHELL` | the zsh startup hook | `1` skips the startup menu in this shell. The utility also sets it for the shells and providers it launches. |
| `AGENT_UTILS_SESSION_NAME` | `agents agent notify send/hook` | Session name for notifications; wins over the agterm name and the folder name. |
| `AGENT_UTILS_ASK_MAX_DEPTH` | `agents agent ask` | How deep asks may nest. Default 1: an asked agent cannot ask again. |
| `AGTERM_UTILS_ROLE` | the coordinator tool gate (`coordinator_gate.py`) | Default `coordinator`. Any other value turns the gate off in that process. Nothing in agent-utils sets it. |
| `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_DATA_HOME` | `core/paths.py`, `core/skills.py` | Base folders, see [Folders](#folders). |

### Internal variables (set by the utility; do not set by hand)

| Variable | Set by | Read by | Meaning |
|---|---|---|---|
| `AGTERM_UTILS_RECORD` | the provider runner (`runner.py`) and restore commands | `agents agterm session ...`, restore, startup | The managed record id of this process; marks a pinned restore command. |
| `AGTERM_UTILS_STARTUP_HANDOFF` | the zsh startup hook | `agents agterm startup` | `1` lets a coordinator handoff ask the hook to close the menu shell (exit 10). |
| `AGTERM_UTILS_MENU_SHOWN` | the zsh startup hook | the zsh startup hook | `1` once the menu ran in this shell. |
| `AGENT_UTILS_ASK_DEPTH` | `agents agent ask` for its children | `agents agent ask` | Current ask depth; refuses a nested ask at the maximum. |
| `AGENT_UTILS_CLAUDE_SESSION_ID` | the Claude session-name hook (through `CLAUDE_ENV_FILE`) | `agents agent rename` | Claude's session id for commands its model runs. |
| `AGENT_UTILS_QWEN_SESSION_ID` | the Qwen session-name hook (through `QWEN_ENV_FILE`) | `agents agent rename` | Qwen's session id. |

### Context variables read from agterm and SSH

| Variable | Meaning here |
|---|---|
| `AGTERM_ENABLED` | `1` inside agterm: `agents` picks the agterm context; the zsh hook runs. |
| `AGTERM_SESSION_ID` | This agterm session: target of renames, status marks and hooks. Also selects the agterm context. |
| `AGTERM_WINDOW_ID`, `AGTERM_WORKSPACE_ID` | The window and workspace of this session, read when launching and recording sessions. |
| `ZELLIJ`, `ZELLIJ_PANE_ID`, `ZELLIJ_SESSION_NAME` | Set by Zellij in every pane: `agents` picks the zellij context, tab commands target this session, and the runner records the pane's tab. |
| `SSH_CONNECTION`, `SSH_TTY` | Without agterm variables: `agents` picks the remote context and `agents remote` runs `terminal`. |

`AGTERMCTL` and `AGTERM_SOCKET` (or `AGT_SOCKET`) point at a specific `agtermctl` and
control socket. Agent session variables (`KIRO_SESSION_ID`, `CODEX_THREAD_ID`,
`OPENCODE_SESSION_ID`, `PI_SESSION_ID` and others) identify the calling agent for
`agents agent rename` and `agents agent ask`.
