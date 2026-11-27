# Projects and providers

agent-utils knows your work as **windows** (agterm windows, such as `Work` and
`Personal`) that hold **projects** (one agterm workspace each, with one or more root
folders and a default agent). **Providers** are the agent CLIs it can launch.

## Add projects

```sh
agents admin project add --project example --name Example \
  --window Work --agent claude --root ~/Projects/example --send
agents admin project add --project hobby --window Personal \
  --agent codex --root ~/Projects/hobby --send
agents agterm list                                    # what is configured
```

- `--project` is the id used on the command line; `--name` is the agterm workspace name
  (default: the id). Workspace names must be unique within a window.
- `--root` repeats for a project that spans several repositories. The first root is the
  starting folder.
- `--agent` sets the project's default agent; without it the window's default applies.
- `--send` creates only that project's window and workspace in agterm, without starting
  an agent. Existing ones with the same name are reused.

Change projects later:

```sh
agents admin project edit --project example --add-root ~/Projects/shared      # one more folder
agents admin project edit --project example --remove-root ~/Projects/shared
agents admin project order --project hobby --position 1      # one-based, within its window
agents admin project push-order                              # config order -> agterm sidebar
agents admin project pull-order                              # agterm sidebar order -> config (after dragging)
agents admin project move --project hobby --window Work      # to another configured window
agents admin project disable --project hobby                 # enable --send brings it back
```

`--root` on `edit` replaces the whole list of roots; `--add-root` and `--remove-root`
change one root and keep the rest. `agents configure` offers the same under
Projects and windows. A project always keeps at least one root.

Order is not synced automatically. agterm can reorder workspaces with
`agtermctl workspace move` (up, down, top, bottom) as well as by dragging, which is how
`push-order` works; `pull-order` copies what you arranged by dragging back into the
configuration. `agents configure` has the same actions on a project or window.

Renaming or moving a project in the configuration does not rename or move existing
agterm rows; old workspaces stay. `agents agterm bootstrap` creates missing workspaces
for every enabled project.

## The configuration file

Projects live in `~/.config/agent-utils/config.json` (`--config PATH` picks another
file). You can edit it by hand; [examples/config.json](../../examples/config.json) is a
starting point:

```json
{
  "version": 1,
  "providers": {"auto_detect": true, "hidden": [], "enabled": []},
  "new_session_position": "default",
  "windows": [
    {"name": "Work", "default_agent": "claude", "projects": [
      {"id": "example", "name": "Example", "roots": ["~/Projects/example"], "enabled": true}
    ]},
    {"name": "Personal", "default_agent": "codex", "projects": []}
  ]
}
```

Arrays keep their order, which is the menu order. Optional keys: per window or project
`agent_order`, per project `default_agent` and `agent_args`, top-level `remotes` (see
[remote.md](remote.md)) and `providers.launch_args`. `agents agterm validate` checks
the file. Edits made through the commands take a lock, validate the whole file and keep
a `config.json.backup-*` copy.

## Choose which providers appear

By default the menus show every supported agent CLI found on PATH. Hide or show one:

```sh
agents admin settings --hide-agent cursor-agent
agents admin settings --show-agent cursor-agent
agents admin settings list
agents admin config set providers.hidden cursor-agent,kimi --yes   # sets the whole list
```

Turning auto-detect off switches to an explicit list; only those providers appear,
installed or not:

```sh
agents admin settings --auto-detect off --enabled-agent claude --enabled-agent pi
agents admin settings --auto-detect on
```

`hidden` applies only with auto-detect and `enabled` only without it; both lists are
kept when you switch. `agents admin settings` with no flags opens the same toggles as a
menu. Visibility only affects menus: `--agent NAME` and resuming a saved
session still work for a hidden provider. Hiding Claude also hides the coordinator
entry.

## Order the provider menu

The default agent comes first, then the rest in built-in order. `agent_order` on a
window or project (the project wins) sets the whole order; `*` stands for every other
visible provider. Without `*`, unlisted providers are left out of that menu.

```sh
agents admin settings --window Work --agent-order 'kiro-cli,claude,codex,*'
agents admin settings --project hobby --agent-order 'codex,*'
agents admin settings --window Work --agent-order ''          # clear
```

## Default launch arguments

```sh
agents admin settings --agent kiro-cli --launch-args="--trust-all-tools"
agents admin settings --agent kiro-cli --launch-args=""        # clear
```

The arguments are stored as an array in `providers.launch_args` and added after the
provider's command (`kiro-cli chat --trust-all-tools`). The utility supplies the
executable, subcommand, roots, resume flags and prompt; no shell runs, so operators
and substitutions are passed literally. A project's `agent_args` replaces the global
arguments for that provider; `"agent_args": {"kiro-cli": []}` opts one project out.
Running processes keep the arguments they started with. Do not put secrets here:
[restore](restore.md) stores launch commands in agterm's state. The repository adds no
permission-bypass flags by default.

## Provider notes

| Menu name | `--agent` | Resume without an id | Resume with `--session ID` | Roots | Initial prompt |
| --- | --- | --- | --- | --- | --- |
| Claude | `claude` | session list, then native picker | `--resume ID` | several | yes |
| Codex | `codex` | session list, then native picker | `resume ID` | several | yes |
| [Kiro CLI](https://kiro.dev) | `kiro-cli` | native picker (`chat --resume-picker`) | `chat --resume-id ID` | one | positional |
| OpenCode | `opencode` | session list (`opencode session list`) | `--session ID` | one | `--prompt` |
| Cursor | `cursor-agent` | native picker (`--resume`) | `--resume ID` | several (`--add-dir`) | positional |
| Pi | `pi` | native picker (`--resume`) | `--session ID` | one | positional |
| Antigravity | `agy` | latest conversation (`--continue`) | `--conversation ID` | several (`--add-dir`) | `--prompt-interactive` |
| Kimi | `kimi` | native picker (`--session`) | `--session ID` | several (`--add-dir`) | not supported |
| Qwen | `qwen` | native picker (`--resume`) | `--resume ID` | several (`--add-dir`) | `--prompt-interactive` |
| GitHub Copilot | `copilot` | native picker (`--resume`) | `--resume=ID` | several (`--add-dir`) | `-i` |
| Mistral Vibe | `vibe` | native picker (`--resume`) | `--resume ID` | several (`--add-dir`) | positional |

- Claude new sessions get a conversation id before launch, so agent-utils can always
  resume them exactly. Codex and Kiro ids are read later by `agents agterm restore sync`.
  For the others, bind the id by hand with `agents agterm session bind`.
- OpenCode can create a session for an unknown id, so copy an existing one when you
  pass `--session`. Picker launches bind the selected id.
- Antigravity offers **Continue most recent session**; use `/resume` inside it for an
  older one.
- Kimi's documented `--prompt` runs without its TUI, so an initial prompt is refused for
  Kimi; type the message in its TUI. Its official installer location
  `~/.kimi-code/bin/kimi` is found even before PATH is refreshed.
- Cursor's `agent` alias is not used; the utility runs `cursor-agent`.
- History listing and adoption exist for Claude and Codex only. Automatic id binding,
  provider-side archive/delete (Codex only), desktop handoff (Claude and Codex) and the
  coordinator (Claude) are not available for the other providers.
- Commands are checked against each vendor's documentation and installed help.
  Sign-in and live resume are not exercised by the tests; try them once per agent you use.

Vendor references: [Kiro CLI](https://kiro.dev/docs/cli/reference/cli-commands/),
[OpenCode](https://opencode.ai/docs/cli/commands), [Cursor](https://cursor.com/docs/cli/using),
[Pi](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md),
[Antigravity](https://antigravity.google/docs/cli-conversations),
[Kimi Code](https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command),
[Qwen Code](https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/),
[GitHub Copilot CLI](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference),
[Mistral Vibe](https://docs.mistral.ai/vibe/code/cli/work-with-cli).
