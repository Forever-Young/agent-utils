# agent-utils

Companion utilities for the agterm terminal and the agent CLIs you run in it: Claude
Code, Codex, [Kiro CLI](https://kiro.dev), OpenCode, Cursor, Pi, Antigravity, Kimi, Qwen, GitHub Copilot and
Mistral Vibe.

- A **session chooser** in every new agterm shell: pick the agent, then start a new
  conversation or resume one of the project's earlier ones.
- **Projects** mapped to agterm windows and workspaces, with per-project default agents,
  roots and launch arguments.
- **Task sessions** in their own git worktrees, a Claude **coordinator**, follow-ups to
  workers and recorded results.
- **Restore**: agent panes come back in their own conversation after agterm reopens.
- **Zellij tabs**: the same project and agent menu opens sessions as tabs, with state on the tab.
- **Remote access** from another Mac, an SSH shell or a phone.
- One **skills store** and one **instruction set** for every agent (rules are markdown files with
  a short header; bundled ones ship in the package, yours go in `~/.config/agent-utils/snippets/`,
  see [shared rules](docs/guide/instructions.md#rule-files)), **session names**
  shared between agterm and the agents, read-only **second opinions** from other agents,
  **push notifications**, and optional **Plannotator Inbox** helpers.

Configuration and state stay outside this repository, in `~/.config/agent-utils` and
`~/.local/state/agent-utils`.

## Install

You need [agterm](https://github.com/umputun/agterm) with `agtermctl` and at least one agent CLI. Then:

```sh
brew tap Forever-Young/tap
brew install Forever-Young/tap/agent-utils
agents --help
```

This installs the `agents` command and the Textual runtime for the menus. Installing
changes no configuration, shell startup or provider files. Update with
`brew upgrade agent-utils`. Installing from a source checkout, rollback and the optional
parts are in [the setup guide](docs/guide/setup.md).

## Let your agent set it up

After installing, the quickest way to configure agent-utils is to have an agent do it and ask
you the questions. Paste this into Claude Code, Codex or any agent you use:

```text
Set up agent-utils on this Mac. First read the README and the guides setup.md,
projects-and-providers.md, instructions.md and skills.md under docs/guide/ in
https://github.com/Forever-Young/agent-utils.
Then interview me, one question at a time with choices: which agent CLIs I use and which
is the default; my projects (folder, agterm window, default agent); whether new agterm
shells should open the session chooser; which shared rules to turn on; whether to share my
skills across agents; whether I want notifications, Zellij tabs or phone access.
Make each change with `agents configure` or the `agents admin ...` commands. Always run the
preview first and show me the diff, and apply only after I say yes. Do not touch provider
settings, shell startup files or credentials without asking. At the end run
`agents admin config list` and summarise what changed and how to undo each change.
```

The agent uses the same previews, backups and undo as you would.

## First run

```sh
agents admin project add --project example --window Work --agent claude \
  --root ~/Projects/example --send          # create the agterm window and workspace
agents admin config run optional zsh-startup --yes   # optional: chooser in every new agterm shell
agents                                      # inside agterm: the session chooser
```

**New agterm shells open the chooser.** The zsh hook adds one marked block to `~/.zshrc`. At the first
prompt of a new agterm shell it runs `agents`, so you pick the agent and a new or earlier conversation,
then land in a normal zsh. Turn it on in `agents configure` under Hooks ("One-shot agterm startup hook
in zsh") or with the command above (run it without `--yes` to preview). To undo, delete the block between the
`BEGIN`/`END agterm-utils zsh startup` markers. It stays off in Zellij and over plain SSH.

A bare `agents` opens the menu that fits where it runs: the session chooser in agterm,
the remote chooser over SSH, otherwise a menu that includes **Settings and actions**
(`agents configure`). `agents --help` lists the command groups.

From a plain SSH shell, `agents zellij` starts or reattaches a persistent workspace.
New tabs offer an agent chooser or a shell. Install the optional mobile profile from
`agents configure` under Hooks ("Mobile Zellij config"). It writes a separate Zellij config for SSH
clients (compact layout, no pane frames, mouse off, detach on close; source in
`agent_utils/contrib/zellij/mobile.kdl`) and never touches your desktop config. UI config paths and the
default session are settings there too.

| Group | For | Commands |
| --- | --- | --- |
| `agents agterm` | inside an agterm session | `startup menu new resume ensure coordinator task session restore history dashboard bootstrap list validate doctor worktree gui cheatsheet` |
| `agents remote` | SSH shell or phone | `menu list attach terminal shell detach lead` |
| `agents agent` | called by agents and hooks | `rename sync ask notify` |
| `agents admin` | configuration and upkeep | `config project settings inbox skills instructions notify` |
| `agents configure` | settings and actions menu | same as `agents admin config tui` |
| `agents zellij` | plain SSH shell or Zellij pane | `run launcher menu open list focus close adopt layout doctor` |
| `agents mobile` | run by the mobile app over SSH | `serve attach pair authorize gate` |

## Documentation

Guides, by task:

- [docs/guide/README.md](docs/guide/README.md): index of the guides
- [docs/guide/setup.md](docs/guide/setup.md): install, update, roll back, optional parts
- [docs/guide/sessions.md](docs/guide/sessions.md): the session chooser, the dashboard, new and resume
- [docs/guide/projects-and-providers.md](docs/guide/projects-and-providers.md): projects, provider visibility, order, launch arguments, per-provider notes
- [docs/guide/tasks-and-workers.md](docs/guide/tasks-and-workers.md): task worktrees, workers, `ensure`, results, the coordinator
- [docs/guide/restore.md](docs/guide/restore.md): reopen agterm into the same conversations
- [docs/guide/remote.md](docs/guide/remote.md): another Mac, SSH, phone, the mobile app helper
- [docs/guide/skills.md](docs/guide/skills.md): the skills store, drift checks, project instruction audits
- [docs/guide/instructions.md](docs/guide/instructions.md): shared instructions and snippets
- [docs/guide/ask-agents.md](docs/guide/ask-agents.md): read-only second opinions
- [docs/guide/session-names.md](docs/guide/session-names.md): one name for agterm and the agent
- [docs/guide/notifications.md](docs/guide/notifications.md): ntfy notifications, Kiro attention hooks
- [docs/guide/inbox.md](docs/guide/inbox.md): the Plannotator Inbox, Kiro reply relay, Tailscale access

Reference, every command and option:

- [docs/reference/README.md](docs/reference/README.md): the `agents` command, groups, conventions
- [docs/reference/agterm.md](docs/reference/agterm.md), [docs/reference/remote.md](docs/reference/remote.md),
  [docs/reference/agent.md](docs/reference/agent.md), [docs/reference/admin.md](docs/reference/admin.md),
  [docs/reference/mobile.md](docs/reference/mobile.md),
  [docs/reference/zellij.md](docs/reference/zellij.md): one page per group
- [docs/reference/setup.md](docs/reference/setup.md): source install script and setup modules
- [docs/reference/files.md](docs/reference/files.md): files and environment variables
- [docs/mobile-protocol.md](docs/mobile-protocol.md): protocol of `agents mobile serve`

Background: [docs/internals/how-it-works.md](docs/internals/how-it-works.md) (modules, storage, input
checks).
`tests/test_docs.py` fails when a command, option or environment variable is missing
from the reference.

## License

MIT, see [LICENSE](LICENSE).
