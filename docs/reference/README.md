# Command reference

Every command, option and file of agent-utils. For walk-throughs, start with the
[guides](../guide/README.md). `tests/test_docs.py` fails when a command, option,
setup module or environment variable has no entry on these pages.

## The `agents` command

```sh
agents                                   # the menu for the detected context
agents [--context agterm|zellij|remote|plain] GROUP COMMAND [OPTIONS]
agents --help                            # groups, the ones for this context first
agents GROUP --help
```

A bare `agents` picks a menu from where it runs:

| Context | Detected by | Bare `agents` opens |
| --- | --- | --- |
| `agterm` | `AGTERM_ENABLED=1` or `AGTERM_SESSION_ID` set | the session chooser (`agents agterm startup`) |
| `zellij` | `ZELLIJ` and `ZELLIJ_PANE_ID` set, unless agterm variables are present and `agtermctl` confirms that agterm session is alive | the project and agent menu, opening a tab (`agents zellij menu`) |
| `remote` | `SSH_CONNECTION` or `SSH_TTY` set, no agterm variables | the remote chooser (`agents remote`) |
| `plain` | neither | a menu: session chooser, remote chooser, **Settings and actions** (`agents configure`), or all commands |

Inside a Zellij pane the zellij context wins, unless agterm variables are set and agterm confirms its
session is alive (a Zellij server started from an agterm shell passes stale agterm variables to every
pane). Otherwise agterm variables win over SSH variables, because agterm does not forward them over SSH. `--context` overrides detection and must come
first. Every group works in every context; the context only changes the bare menu and
the help order.

## Groups

| Group | Runs | Commands | Page |
| --- | --- | --- | --- |
| `agents agterm` | inside an agterm session | `startup menu new resume ensure coordinator task session restore history dashboard bootstrap list validate doctor worktree gui cheatsheet` | [agterm.md](agterm.md) |
| `agents remote` | in an SSH shell or on a phone | `menu list attach terminal shell detach lead` | [remote.md](remote.md) |
| `agents agent` | called by agents and hooks | `rename sync ask notify` | [agent.md](agent.md) |
| `agents admin` | configuration and upkeep | `config project settings inbox skills instructions notify` | [admin.md](admin.md) |
| `agents configure` | anywhere | the settings and actions menu (= `agents admin config tui`) | [admin.md](admin.md#agents-configure) |
| `agents zellij` | in a Zellij pane | `menu open list focus close adopt layout doctor` | [zellij.md](zellij.md) |
| `agents mobile` | by the mobile app over SSH | `serve attach pair authorize gate` | [mobile.md](mobile.md) |

`agents agent notify` and `agents admin notify` are the same command; both accept
every notify subcommand. Agents and hooks use the `agent` route for `send` and `hook`.

The older `project-session` command and the old `agents` host commands are gone, with
no aliases: use `agents agterm ...` and `agents remote ...`.

## Other pages

- [setup.md](setup.md): `scripts/install.py`, `scripts/brew-local.py`, `scripts/release.py` and the opt-in
  setup modules in `agent_utils/core/setup` (`scripts/configure-*.py`).
- [files.md](files.md): configuration, state and install files, and every environment
  variable.
- [../mobile-protocol.md](../mobile-protocol.md): the JSON-lines protocol of
  `agents mobile serve`.

## Common conventions

- Commands that change files preview first. The apply flag is `--apply` (skills,
  setup modules) or `--yes` (config, instructions, Inbox edits, session forget); each
  page says which. A few commands write at once and keep a backup; their entries say so.
- `--dry-run` goes before the command (`agents agterm --dry-run new ...`,
  `agents admin --dry-run inbox dispatch`) and prints what would run.
- Configuration and state live outside the repository, in `~/.config/agent-utils` and
  `~/.local/state/agent-utils` (XDG variables are honored). See [files.md](files.md).
- Errors print `Error: ...` on stderr and exit 1.
