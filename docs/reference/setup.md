# Setup modules and scripts

The installer and every opt-in configuration (hooks, launchd job, shell startup) live in
`agent_utils/core/setup`. Each module has a `main(argv)` and runs as
`python3 -m agent_utils.core.setup.NAME`; each `scripts/*.py` file is a thin wrapper with
the same options. Walk-through: [setup guide](../guide/setup.md).

## Common rules

- **Preview, apply, remove.** Every module only prints what it would do until you pass
  `--apply`. Modules with `--remove` undo their change; `--remove` alone previews the
  removal and `--remove --apply` performs it.
- **Backups.** Before a file is changed, a copy is written beside it as
  `FILE.backup-YYYYMMDDTHHMMSSffffffZ` (UTC). The installer instead snapshots the code
  into `~/.bin/agterm-tools/backups/STAMP/`.
- **Undo.** Use `--remove --apply` where it exists; otherwise restore the newest backup
  by hand, as listed per module.
- **Install first.** Hooks run the installed copy of the package
  (`~/.bin/agterm-tools/agent_utils`, or the Homebrew package when that copy is absent),
  so run the installer before the hook modules. A module warns when the hook script is
  missing.
- **Exit codes.** 0 on success. A module that refuses (unmanaged setting, Live restore,
  already up to date) exits 1 with a message.

## Running them

From a checkout:

```sh
python3 scripts/configure-kiro.py              # preview
python3 scripts/configure-kiro.py --apply
python3 -m agent_utils.core.setup.kiro --apply # same, from any folder where the package imports
```

From `agents`: some modules are actions of [`agents admin config run`](admin.md). An
action previews, and `--yes` adds the module's `--apply`:

```sh
agents admin config run optional-list
agents admin config run optional kiro-hooks --yes
agents admin config run install --yes
```

With Homebrew: use the `agents admin config run` actions above. For a module that has no
action, the formula's Python has the package:

```sh
$(brew --prefix agent-utils)/libexec/bin/python -m agent_utils.core.setup.optional
$(brew --prefix agent-utils)/libexec/bin/python -m agent_utils.core.setup.zellij_status --apply
```

Use one install channel per machine. Do not run the installer from a Homebrew install;
Homebrew upgrades go through [`scripts/brew-local.py`](#scriptsbrew-localpy).

| Module | Wrapper | `agents admin config run` action |
|---|---|---|
| `agent_utils.core.setup.installer` | `scripts/install.py` | `install`, `install-backups`, `rollback` |
| `agent_utils.core.setup.kiro` | `scripts/configure-kiro.py` | through `optional kiro-hooks` |
| `agent_utils.core.setup.relay` | `scripts/configure-relay.py` | through `optional inbox-relay` |
| `agent_utils.core.setup.session_names` | `scripts/configure-session-names.py` | through `optional session-names` |
| `agent_utils.core.setup.zellij_status` | `scripts/configure-zellij-status.py` | through `optional zellij-status` or `optional zellij-adopt` |
| `agent_utils.core.setup.zsh` | `scripts/configure-zsh.py` | through `optional zsh-startup` |
| `agent_utils.core.setup.startup` | `scripts/configure-startup.py` | none |
| `agent_utils.core.setup.optional` | `scripts/configure-optional.py` | `optional ITEM`, `optional-list` |
| (not a module) | `scripts/brew-local.py`, `scripts/release.py` | none |

## Modules

### `agent_utils.core.setup.installer`

Wrapper: `scripts/install.py`. Installs the code: the package to
`~/.bin/agterm-tools/agent_utils`, the `agents` command to `~/.bin/agents`, and with
`--with-tui` or `--with-pdf` an isolated runtime in `~/.bin/agterm-tools/runtime`. It
never edits project, shell or terminal configuration and never applies optional
configurations. Actions: `install` (= `--with-tui`), `install-backups`
(= `--list-backups`), `rollback` (= `--rollback`).

```sh
python3 scripts/install.py --with-tui          # preview
python3 scripts/install.py --with-tui --apply
python3 scripts/install.py --list-backups
python3 scripts/install.py --rollback --apply
```

| Option | Meaning |
|---|---|
| `--apply` | Install (or roll back). Without it, print the targets only. |
| `--bin-dir DIR` | Where `agents` and `agterm-tools/` go. Default `~/.bin`. |
| `--with-tui` | Install pinned Textual 8.2.8 into the isolated runtime (a venv, reused when present). |
| `--with-pdf` | Also install reportlab 4.5.1 (PDF cheatsheet) into the runtime. |
| `--rollback [STAMP]` | Restore a code snapshot: the latest, or the named timestamp from `--list-backups`. |
| `--list-backups` | Print the snapshot names and exit. |
| `--source DIR` | Folder that contains `agent_utils`. Default: this checkout; when run from the installed copy, the source recorded in `install-manifest.json`. |

Every apply first snapshots the current code, wrapper and manifest into
`~/.bin/agterm-tools/backups/STAMP/`. Install writes
`~/.bin/agterm-tools/install-manifest.json` (time, backup, bin dir, source), removes
the old `project-session` and `agent-utils` wrappers, and keeps forwarders in
`~/.bin/agterm-tools/agterm_utils/` for hooks written before the rename. Rollback
restores code and wrappers only; config, state, worktrees, provider history and the
runtime stay. It warns when `agents` on PATH comes from Homebrew.

### `agent_utils.core.setup.kiro`

Wrapper: `scripts/configure-kiro.py`.
Writes the managed [Kiro CLI](https://kiro.dev) hook file `~/.kiro/hooks/agterm-utils-attention.json`, which runs the installed `agent_utils/agterm/kiro_hook.py`: attention on `PermissionRequest` (needs Kiro CLI 3.4.0+), active on prompt submit and tool use, completed on `Stop`, plus the Inbox reply hooks (`SessionStart`, `SessionEnd`, `inbox-learn`, `inbox-check`).
Restart Kiro sessions afterwards.

```sh
python3 scripts/configure-kiro.py
python3 scripts/configure-kiro.py --apply
python3 scripts/configure-kiro.py --apply --remove   # undo
```

| Option | Meaning |
|---|---|
| `--apply` | Write (or with `--remove`, delete) the hook file. |
| `--remove` | Remove the hook file instead. |
| `--home DIR` | Home folder to write under. Default: your home. |

An existing file is backed up beside itself before it is replaced or removed. When the
file is already current, `--apply` exits 1 with `Already up to date.`

### `agent_utils.core.setup.relay`

Wrapper: `scripts/configure-relay.py`. Writes and loads the launchd job
`~/Library/LaunchAgents/agent-utils.inbox-relay.plist` (label
`agent-utils.inbox-relay`), which keeps `agents admin inbox relay` running: starts at
login, restarts on exit (throttled to 30 s), logs to
`~/.local/state/agent-utils/inbox-relay.log`. PATH for the job holds the folders of
`agtermctl` and `plannotator` plus `/usr/local/bin:/usr/bin:/bin`. See the
[Inbox guide](../guide/inbox.md).

```sh
python3 scripts/configure-relay.py
python3 scripts/configure-relay.py --apply
python3 scripts/configure-relay.py --remove --apply  # undo
```

| Option | Meaning |
|---|---|
| `--apply` | Write the plist, `launchctl bootout` any old job, `launchctl bootstrap` the new one. Exit 1 when bootstrap fails. |
| `--remove` | Unload (`launchctl bootout`) and delete the plist. |
| `--home DIR` | Home folder for the plist, the `agents` lookup (`~/.bin/agents`, else PATH) and the log. Default: your home. |

No backup is kept; the plist is generated, so `--apply` recreates it.

### `agent_utils.core.setup.zellij_status`

Wrapper: `scripts/configure-zellij-status.py`. Adds hooks that show agent state (working, needs you, done)
on the Zellij tab; see [agents zellij](zellij.md#tab-status). Only entries that run `core/zellij_status.py`
are added or removed. Outside Zellij the hooks do nothing. Kiro uses its own hook file instead.

| Agent | File | Hooks |
|---|---|---|
| Claude | `~/.claude/settings.json` | `UserPromptSubmit`, `PostToolUse`, `PermissionRequest`, `Notification`, `Stop` |
| Qwen | `~/.qwen/settings.json` | the same without `Notification` (untested) |
| Codex | `~/.codex/hooks.json` | the same without `Notification`; trust them once with `/hooks` |
| Pi | `~/.pi/agent/extensions/agent-utils-zellij-status.ts` | symlink to the installed `core/pi_zellij_status.ts`: `agent_start` working, `agent_settled` done |
| OpenCode v2 | `~/.config/opencode/plugins/agent-utils-zellij-status/tui.js` | symlink to the installed `core/opencode_zellij_status.js`, a TUI plugin: permission or form waiting, turn running, done, error |

The preview names each file and the hooks it adds (`Stop (done)`, `SessionStart (adopt)`, ...). The Pi and
OpenCode files run inside the agent and write the status to the pane's terminal themselves; OpenCode loads the
plugin directory by itself (as agterm's own status plugin does), which is not yet confirmed live.

```sh
python3 scripts/configure-zellij-status.py
python3 scripts/configure-zellij-status.py --apply
python3 scripts/configure-zellij-status.py --apply --remove   # undo
```

| Option | Meaning |
|---|---|
| `--apply` | Write the planned files (backups beside each). |
| `--remove` | Remove our entries instead. |
| `--home DIR` | Home folder to configure. Default: your home. |
| `--all` | Configure agents even when their executable is not found. |
| `--adopt` | Also add a `SessionStart` hook that runs `agents zellij adopt --self` for Claude and Codex, and the Kiro file `~/.kiro/hooks/agent-utils-zellij-adopt.json`. With `--remove`, the Kiro file is removed too. |

### `agent_utils.core.setup.session_names`

Wrapper: `scripts/configure-session-names.py`. Adds the hooks behind
[`agents agent rename`](agent.md#agents-agent-rename). Only agents whose executable is
found (or whose file already exists) are configured, and only entries that run
`agent_utils/agterm/names.py` are added or removed.

| Agent | File | Hooks |
|---|---|---|
| Claude | `~/.claude/settings.json` | `SessionStart`, `UserPromptSubmit` |
| Qwen | `~/.qwen/settings.json` | `SessionStart`, `UserPromptSubmit` (untested on current Qwen) |
| Codex | `~/.codex/hooks.json` | `SessionStart` (startup, resume, fork, clear), `UserPromptSubmit`; trust them once with `/hooks` |
| Kiro | `~/.kiro/hooks/agent-utils-session-names.json` | `SessionStart`, `UserPromptSubmit` |
| Pi | `~/.pi/agent/extensions/agent-utils-session-name.ts` | symlink to the installed `pi_session_name.ts` |

```sh
python3 scripts/configure-session-names.py
python3 scripts/configure-session-names.py --apply
python3 scripts/configure-session-names.py --apply --remove   # undo
```

| Option | Meaning |
|---|---|
| `--apply` | Write the planned files. |
| `--remove` | Remove our entries (and the Kiro file and Pi link) instead. |
| `--home DIR` | Home folder. Default: your home. |
| `--all` | Configure every agent even when its executable is not found. |

Each changed regular file is backed up beside itself; the Pi symlink is replaced
without a backup. Restart agent sessions afterwards.

### `agent_utils.core.setup.zsh`

Wrapper: `scripts/configure-zsh.py`. The one-shot agterm startup hook: copies
`agterm-startup.zsh` to `~/.bin/agterm-tools/agterm-startup.zsh` and puts a managed
block (`# BEGIN agterm-utils zsh startup` ... `# END ...`) at the top of `~/.zshrc`
that sources it. At the first prompt of an interactive agterm shell the hook runs
`agents agterm startup` once. zsh stays agterm's shell, so Live sessions keep working.
It also removes a block that `startup` wrote to `~/.config/agterm/ghostty.conf`, and
refuses when `ghostty.conf` has an unmanaged `command = ... agents agterm startup` line.

```sh
python3 scripts/configure-zsh.py
python3 scripts/configure-zsh.py --apply
```

| Option | Meaning |
|---|---|
| `--apply` | Write the helper and `~/.zshrc` (and `ghostty.conf` when it held the old block). |
| `--home DIR` | Home folder. Default: your home. |

There is no `--remove`. Undo: delete the managed block from `~/.zshrc`, or restore
`~/.zshrc.backup-STAMP`. `.zshrc`, the helper and `ghostty.conf` are backed up beside
themselves before each apply. Existing shells are unchanged.

### `agent_utils.core.setup.startup`

Wrapper: `scripts/configure-startup.py`. The alternative agterm default-command
integration: appends a managed block with `command = direct:LAUNCHER agterm startup` to
`ghostty.conf`. It is not compatible with Live-session persistence, so it refuses when
agterm's restore mode is Live; use `zsh` instead in that case.

```sh
python3 scripts/configure-startup.py
python3 scripts/configure-startup.py --apply
```

| Option | Meaning |
|---|---|
| `--apply` | Write the block. Needs the launcher file, `agtermctl`, and a restore mode other than Live. |
| `--config-dir DIR` | agterm config folder holding `ghostty.conf`. Default `~/.config/agterm`. |
| `--launcher PATH` | The `agents` command to run. Default `~/.bin/agents`. Must not contain whitespace. |

It refuses when `ghostty.conf` already has an unmanaged `command =` line. No
`--remove`; undo by restoring `ghostty.conf.backup-STAMP` or deleting the block
(`configure-zsh.py --apply` also removes it).

### `agent_utils.core.setup.optional`

Wrapper: `scripts/configure-optional.py`. Lists the optional configurations with their
detection and status (`configured`, `available`, `unavailable (why)`) and runs the
chosen modules as `python -m agent_utils.core.setup.MODULE --home HOME [--apply]`.
Actions: `optional-list` (list) and `optional ITEM` (one item; `--yes` applies).

| Item | Module | Available when |
|---|---|---|
| `kiro-hooks` | `kiro` | `kiro-cli` on PATH or `~/.kiro` exists |
| `inbox-relay` | `relay` | Kiro found, `agtermctl` and `plannotator` on PATH, and `kiro-hooks` configured |
| `session-names` | `session_names` | always |
| `zellij-status` | `zellij_status` | always |
| `zellij-adopt` | `zellij_status --adopt` | always |
| `zsh-startup` | `zsh` | always |

```sh
python3 scripts/configure-optional.py
python3 scripts/configure-optional.py --detected --apply
python3 scripts/configure-optional.py inbox-relay --apply
```

| Option | Meaning |
|---|---|
| `NAMES` | Items to run. None: list only. |
| `--apply` | Pass `--apply` to each chosen module. |
| `--detected` | Also choose every available item that is known to be unconfigured (`kiro-hooks`, `inbox-relay`; the status of the others is not detected). |
| `--home DIR` | Home folder passed to each module. Default: your home. |

Items run in table order, so `kiro-hooks` goes before `inbox-relay`. It exits 0 and
prints the exit code of a module that failed (an up-to-date module also exits 1). Each
item keeps its own undo.

## Other scripts

### `scripts/brew-local.py`

Builds and installs agent-utils through Homebrew from the local repository only. It
archives the committed `HEAD` (`git archive`) into a tarball, renders
`packaging/homebrew/agent-utils.rb` with a `file://` URL into a local tap
`local/agent-utils` (no remote), then runs `brew install` or `brew reinstall
--build-from-source`. Version: `pyproject.toml` version plus the short commit. Nothing
is published or pushed. Textual is always included.

```sh
python3 scripts/brew-local.py           # preview: version, tarball path, tap
python3 scripts/brew-local.py --apply
brew test agent-utils
```

| Option | Meaning |
|---|---|
| `--apply` | Build the tarball, create the tap if needed, write the formula, install or reinstall. |
| `--cache-dir DIR` | Where tarballs go. Default `~/.cache/agent-utils/brew`. |
| `--allow-dirty` | Build even with uncommitted changes (they are not included). Without it, a dirty tree exits 1. |

Update: commit, then `--apply` again. Undo: `brew uninstall agent-utils; brew untap
local/agent-utils`; config, state and history stay. Remove `~/.bin/agents` first if
`install.py` created it. Brew never changes shell startup or hooks.

### `scripts/release.py`

Cuts a release of the repository it runs in. It archives the committed `HEAD`
(`git archive`) into `dist/agent-utils-VERSION.tar.gz`, where VERSION is `version` in
`pyproject.toml`, creates the GitHub release `vVERSION` with that file as its asset
(`gh release create`, which also creates the tag), and writes `url`, `version` and `sha256`
into `packaging/homebrew/agent-utils-tap.rb` and into the tap checkout's
`Formula/agent-utils.rb`. The formula therefore installs exactly the uploaded file. The tap
is not committed or pushed; review it, commit and push yourself.

The release notes are the `## [VERSION]` section of `CHANGELOG.md` plus a link comparing
the version with the previous release (`Full changelog: .../compare/vOLD...vNEW`). The
script stops, in the preview too, when `CHANGELOG.md` has no section for `version`.

```sh
python3 scripts/release.py           # preview: version, tag, asset URL
python3 scripts/release.py --apply
```

| Option | Meaning |
|---|---|
| `--apply` | Build the tarball, create the release and write the formulas. |
| `--repo OWNER/NAME` | GitHub repository. Default `Forever-Young/agent-utils`. |
| `--out-dir DIR` | Where the tarball goes. Default `dist/`. |
| `--tap-dir DIR` | Tap checkout. Default `brew --repository Forever-Young/tap`. |
| `--allow-dirty` | Release `HEAD` even with uncommitted changes (they are not included). |

It refuses when the release already exists: raise `version` in `pyproject.toml` first and
add the matching `CHANGELOG.md` section.
Needs `gh` signed in.

### Wrapper scripts

`scripts/install.py`, `scripts/configure-kiro.py`, `scripts/configure-relay.py`,
`scripts/configure-session-names.py`, `scripts/configure-zellij-status.py`, `scripts/configure-zsh.py`,
`scripts/configure-startup.py` and `scripts/configure-optional.py` each import the
package (from the checkout when it is not installed) and call the module's `main()`.
They take exactly the module's options.
