# Setup

## Requirements

- Python 3.9 or later. The core needs only the standard library.
- agterm with `agtermctl` on PATH, for everything in `agents agterm`.
- At least one supported agent CLI (Claude Code, Codex, [Kiro CLI](https://kiro.dev), OpenCode, Cursor, Pi,
  Antigravity, Kimi, Qwen, Copilot, Mistral Vibe), installed and signed in through its
  vendor's instructions. agent-utils never installs or configures a provider.
- Optional: [Textual](https://github.com/Textualize/textual) for full-screen menus (the
  installer puts it in an isolated runtime), `fzf` as the second-choice menu, `reportlab`
  for a PDF hotkey cheatsheet.

## Install with Homebrew

```sh
brew tap Forever-Young/tap
brew install Forever-Young/tap/agent-utils
agents --help
```

The formula installs the `agents` command with Textual for the full-screen menus, from a
release tarball of this repository. Without Textual the menus use fzf or a numbered list.
Update with `brew upgrade agent-utils`. Then follow "Let your agent set it up" in the
[README](../../README.md). Use either Homebrew or the source install below on one machine,
not both. Run `agents agterm doctor` to see which provider executables, `agtermctl` and
`fzf` it finds.

Installing changes no shell startup, hooks, provider settings or configuration. Those are
separate opt-in steps, below.

## Install from a source checkout

For development, or to run unreleased changes:

```sh
python3 -m unittest discover -s tests
python3 scripts/install.py --with-tui          # preview: what would be written
python3 scripts/install.py --with-tui --apply  # install or update
```

This writes one command, `~/.bin/agents`, and the package, helpers and the Textual
runtime under `~/.bin/agterm-tools`. Add `~/.bin` to PATH. Without `--with-tui` there is
no Textual. A runtime that is already installed is reused. `--with-pdf` adds reportlab to
the same runtime (for the PDF hotkey cheatsheet); the flags combine. `install.py` warns
when `agents` on PATH comes from Homebrew.

To update, pull or edit the checkout and run `python3 scripts/install.py --with-tui --apply`
again. Every install keeps a code snapshot:

```sh
python3 scripts/install.py --list-backups
python3 scripts/install.py --rollback          # preview the latest complete snapshot
python3 scripts/install.py --rollback --apply
```

Rollback restores the code and wrappers only. Configuration, state, provider history,
worktrees and the Textual runtime stay as they are. The installer also records the
source folder, so `agents admin config run install\|rollback` works
without changing to the checkout.

### Build a Homebrew formula from a checkout

`scripts/brew-local.py` builds a formula from the committed `HEAD` into a local tap
(`local/agent-utils`, no remote). Nothing is pushed, and Textual is always included.

```sh
python3 scripts/brew-local.py            # preview: version, tarball path, tap
python3 scripts/brew-local.py --apply    # build, write the formula, brew install or reinstall
brew test agent-utils
```

Update: commit, then run `--apply` again. Undo: `brew uninstall agent-utils; brew untap
local/agent-utils` (configuration, state and history stay). Remove `~/.bin/agents` first
if `install.py` created it.

### Releases

The published formula (`packaging/homebrew/agent-utils-tap.rb`) installs the tarball of a
GitHub release. `scripts/release.py` builds that tarball, creates the release with `gh` and
writes the tap formula; see [the reference](../reference/setup.md#scriptsreleasepy).

## Choose the menu backend

Menus use Textual in a terminal when it is installed, then fzf, then a numbered list.
Set `AGTERM_UTILS_MENU=auto|textual|fzf|plain` to force one. In every backend, typing
filters the list (Textual and fzf), Enter selects and Escape cancels.

## Settings and actions in one place

```sh
agents configure                       # full-screen settings app (Textual); --ui menu for the list menu
agents admin config list               # the same, as text
agents admin config actions            # actions you can run
agents admin config run optional       # list the optional configurations and their state
```

`agents configure` is the same as `agents admin config tui`. Every menu entry is also a
command; see [admin.md](../reference/admin.md#agents-admin-config). A setting change
previews a diff and writes with `--yes`, keeping a backup that `agents admin config undo`
restores.

## Optional parts

Each optional part has its own preview, apply and undo. None is applied by the installer.
Run a command without `--yes` to preview it.

| Part | What it does | Turn on | Guide |
| --- | --- | --- | --- |
| zsh startup hook | shows the session chooser in every new agterm shell | `agents admin config run optional zsh-startup --yes` | [sessions.md](sessions.md#show-the-chooser-in-every-new-shell) |
| Kiro hooks | marks Kiro sessions blocked/active/completed, sends notifications, Inbox reply pointers | `agents admin config run optional kiro-hooks --yes` | [notifications.md](notifications.md) |
| Session-name hooks | keeps agterm and agent session names in step | `agents admin config run optional session-names --yes` | [session-names.md](session-names.md) |
| Inbox relay job | launchd job that wakes idle Kiro sessions on Inbox replies | `agents admin config run optional inbox-relay --yes` | [inbox.md](inbox.md#wake-kiro-on-a-reply) |
| Shared instructions | one rule set for every agent | `agents admin instructions apply --yes` | [instructions.md](instructions.md) |
| Skills store | one copy of each skill for every agent | `agents admin skills sync --apply` | [skills.md](skills.md) |
| Push notifications | ntfy notifications from agents | `agents admin notify url ...`, then `enable` | [notifications.md](notifications.md) |

`agents admin config run optional-list` lists the hook and job parts (`kiro-hooks`,
`inbox-relay`, `session-names`, `zsh-startup`, ...) with their detection and state. Each
part is a module in `agent_utils/core/setup`; the reference, including applying every
detected part at once, is [setup.md](../reference/setup.md).

## Where things live

Configuration is in `~/.config/agent-utils`, state in `~/.local/state/agent-utils`
(both follow the XDG variables). Neither is in the repository. A first run copies an
older `agterm-utils` directory once and leaves the old one as a backup. The full list
is in [files.md](../reference/files.md).

Next: describe your projects in [projects-and-providers.md](projects-and-providers.md),
then open sessions as in [sessions.md](sessions.md).
