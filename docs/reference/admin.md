# agents admin

`agents admin` holds configuration and upkeep: every setting in one view, projects and
provider visibility, the Plannotator Inbox, the skills store, shared agent instructions,
and notifications. It runs in any terminal; nothing here needs an
agterm session except the commands that open rows (`project ... --send`, `inbox
dispatch`, `inbox adopt`, `inbox relay`). `agents configure` is the interactive menu of
`agents admin config`.

Configuration lives outside the repository, in `~/.config/agent-utils/` (XDG config
dir), state in `~/.local/state/agent-utils/` (XDG state dir).

## `agents admin config`

The command-line view of every agent-utils setting, like `git config`. Bare `agents
admin config` prints help. Every change previews a diff until `--yes`; a write takes the
file's lock, validates the whole file, backs it up to `FILE.backup-STAMP` beside it and
writes atomically. Guide: [projects and providers](../guide/projects-and-providers.md).

```sh
agents admin config [list|get|set|unset|undo|actions|run|tui] ...
```

Editable settings (the `SETTINGS` registry in `agent_utils/core/settings.py`):

| Key | File | Kind and allowed values | Default |
| --- | --- | --- | --- |
| `providers.auto_detect` | `config.json` | bool: `on`/`off` (also `true`/`false`, `yes`/`no`, `1`/`0`). Off: menus offer only `providers.enabled` | on |
| `providers.hidden` | `config.json` | comma list of agents left out of menus while auto-detect is on | empty |
| `providers.enabled` | `config.json` | comma list of agents offered while auto-detect is off | empty |
| `providers.launch_args.AGENT` | `config.json` | shell words, extra arguments for one agent at launch; `AGENT` is a supported agent id such as `kiro-cli` | none; `config list` and the configurator show every supported agent |
| `new_session_position` | `config.json` | `default` or `top` (new agterm rows go to the top once per row) | `default` |
| `coordinator_name` | `config.json` | name of the coordinator Claude session; a project may also set its own `coordinator_name` | `Main` |
| `notify.enabled` | `notify.json` | bool; needs `notify.url` first | off |
| `notify.url` | `notify.json` | ntfy URL, `https://host/topic` | empty |
| `notify.away_after_seconds` | `notify.json` | whole seconds, 0 to 86400; 0 always sends | 120 |
| `notify.away_when_locked` | `notify.json` | bool | on |
| `notify.title` | `notify.json` | text template | `{session}` |
| `notify.body` | `notify.json` | text template | `{event_text}{detail_suffix}` |
| `snippets.variables.NAME` | `snippets.json` | text, the value of `{{NAME}}` in instruction snippets | empty |

Agent ids: `claude`, `codex`, `kiro-cli`, `opencode`, `cursor-agent`, `pi`, `agy`,
`kimi`, `qwen`, `copilot`, `vibe`. Settings owned by other commands (projects, agent
order, SSH remotes, skills policy, instruction sources, schedules, hooks, the install)
are listed with their state and the command that changes them. agterm and provider files
(`~/.config/agterm`, `~/.claude/settings.json`, `~/.codex/config.toml`,
`~/.kiro/settings`) are listed read-only.

### `agents admin config list`

Prints every setting with its value (`[default]` when unset), then the settings other
commands own.

```sh
agents admin config list [--section SECTION] [--json]
```

| Option | Meaning |
| --- | --- |
| `--section SECTION` | Only one section: `providers`, `placement`, `notify`, `snippets`, `projects`, `skills`, `instructions`, `schedules`, `hooks`. Default: all. |
| `--json` | Print `{"settings": [...], "managed": [...]}` instead of text. |

### `agents admin config get`

Prints one value. Lists and argument settings print as JSON.

```sh
agents admin config get KEY
```

| Option | Meaning |
| --- | --- |
| `KEY` | A key from the table, for example `providers.auto_detect` or `providers.launch_args.kiro-cli`. |

### `agents admin config set`

Changes one value. Previews the diff; `--yes` writes it and keeps a backup.

```sh
agents admin config set KEY VALUE [--yes]
agents admin config set new_session_position top --yes
```

| Option | Meaning |
| --- | --- |
| `KEY` | The setting; a family key needs its member (`providers.launch_args.codex`, `snippets.variables.owner`). |
| `VALUE` | Parsed by kind: `on`/`off`, a choice, `a,b` for agent lists, shell words for launch arguments. |
| `--yes` | Write the change. Without it only the diff is shown. |

### `agents admin config unset`

Removes one value so the default applies again. Previews unless `--yes`.

```sh
agents admin config unset KEY [--yes]
```

| Option | Meaning |
| --- | --- |
| `KEY` | The setting to remove. |
| `--yes` | Write the change. |

### `agents admin config undo`

Restores the newest `FILE.backup-STAMP` of one file. The current file is backed up
first, so an undo can be undone; the restored backup is deleted.

```sh
agents admin config undo config|notify|snippets [--yes]
```

| Option | Meaning |
| --- | --- |
| `config` | `~/.config/agent-utils/config.json`. |
| `notify` | `~/.config/agent-utils/notify.json`. |
| `snippets` | `~/.config/agent-utils/snippets.json`. |
| `--yes` | Restore. Without it the diff against the backup is shown. |

### `agents admin config actions`

Lists the runnable actions with their section and mode (`read-only`, `preview, --yes
applies`, `needs --yes`).

```sh
agents admin config actions
```

| Action | Runs | Apply flag added by `--yes` |
| --- | --- | --- |
| `cheatsheet` | `agents agterm cheatsheet --open` (see [agterm.md](agterm.md#agents-agterm-cheatsheet)) | acts at once; `run` refuses it without `--yes` |
| `skills-status` | `agents admin skills status` | none, read-only |
| `skills-sync` | `agents admin skills sync` | `--apply` |
| `skills-check` | `agents admin skills check` (basic check) | none, read-only |
| `instructions-status` | `agents admin instructions status` | none, read-only |
| `instructions-apply` | `agents admin instructions apply` | `--yes` |
| `instructions-update` | `agents admin instructions compose --update` (refresh stale snippet copies in `common.md`, then apply) | `--yes` |
| `schedule-install` | `agents admin skills schedule install` (defaults: every 1d, no judge) | `--apply` |
| `schedule-remove` | `agents admin skills schedule remove` | `--apply` |
| `notify-show` | `agents admin notify show` | none, read-only |
| `notify-test` | `agents admin notify test` | acts at once; `run` refuses it without `--yes` |
| `inbox-doctor` | `agents admin inbox doctor` | none, read-only |
| `optional` | `python3 -m agent_utils.core.setup.optional ITEM`; needs an item name (`kiro-hooks`, `inbox-relay`, `zsh-startup`, ...) | `--apply` |
| `remove-kiro-hooks` | `python3 -m agent_utils.core.setup.kiro --remove` | `--apply` |
| `remove-inbox-relay` | `python3 -m agent_utils.core.setup.relay --remove` | `--apply` |
| `remove-session-names` | `python3 -m agent_utils.core.setup.session_names --remove` | `--apply` |
| `remove-zellij-status` | `python3 -m agent_utils.core.setup.zellij_status --remove` | `--apply` |
| `optional-list` | `python3 -m agent_utils.core.setup.optional` | none, read-only |
| `install` | `python3 -m agent_utils.core.setup.installer --with-tui` | `--apply` |
| `install-backups` | `python3 -m agent_utils.core.setup.installer --list-backups` | none, read-only |
| `rollback` | `python3 -m agent_utils.core.setup.installer --rollback` | `--apply` |

### `agents admin config run`

Runs one action as a child process and shows its output. The exit code is the child's.

```sh
agents admin config run ACTION [ARG] [--yes]
agents admin config run optional kiro-hooks --yes
```

| Option | Meaning |
| --- | --- |
| `ACTION` | An action id from the table above. |
| `ARG` | The argument for an action that takes one (`optional`); refused for the others. |
| `--yes` | Add the action's apply flag, or run a `needs --yes` action. Without it the action previews. |

### `agents admin config tui`

Opens the configurator. Sections: Agents, agterm settings, Notifications, Projects and
windows, Personalization (values used in shared instruction snippets), Skills, Shared rules,
Scheduled jobs and Hooks. Every entry does its work in place: a setting or a project edit
(enable or disable, rename, root, move to another window, default agent, agent order, Zellij
layout and session, add a project) shows its diff and asks before writing; a hook or a
scheduled job is previewed, then applied or removed; each of your own skills can be
excluded or included per agent, marked reviewed or removed, and a skill can be added; each
shared-rule snippet is listed with its summary, on/off state and whether it is bundled or
`(custom)`, shows its text and file before you choose (Personalization lists the variables the rules use, with their meaning, and lets you add your own), and can be turned on, off, updated or
replaced; "Add a custom rule" writes a new file in `~/.config/agent-utils/snippets/`; the skills
section also starts the drift check or a project instruction audit with an agent review (the change goes to `common.md` and to every agent, as
`agents admin instructions compose` does); an action runs its preview and asks
before applying. Install and rollback of agent-utils itself are only reachable with
`agents admin config run install|rollback`. The menu uses Textual when it is installed and both
stdin and stdout are terminals, else fzf, else a numbered list (`AGTERM_UTILS_MENU`
forces `textual`, `fzf` or `plain`).

```sh
agents admin config tui [--ui auto|textual|menu]
agents configure [--ui ...]   # the same
```

| Option | Meaning |
| --- | --- |
| `--ui auto` | Default. The full-screen Textual app when Textual is installed and stdin and stdout are terminals, else the list menu. |
| `--ui textual` | The full-screen app; an error when Textual is missing or there is no terminal. |
| `--ui menu` | The list menu (fzf or a numbered list; `AGTERM_UTILS_MENU` still picks the picker). |

The full-screen app (`agent_utils/core/configure_tui.py`) has the sections on the left, the
entries of the chosen section in the middle, and a log
below. Enter opens a setting or runs an action, Tab switches panes, `u` undoes the last change
of a file, `r` reloads, `q` quits. A setting change shows its diff in a confirmation box and
writes only on `y`. An action runs its preview with the output streaming into the log, then
asks before applying. It makes exactly the same changes as `config set` and `config run`.

### `agents configure`

Short form of `agents admin config tui`; the plain-terminal `agents` menu offers it as
**Settings and actions**. It takes the same `--ui` option.

```sh
agents configure
```

## `agents admin project` and `agents admin settings`

These commands go through the flat `agents agterm` parser (`agent_utils/cli.py`), with
handlers in `agent_utils/agterm/configuration.py`. They edit `config.json` directly: there
is no preview step. Each edit takes the file's lock, validates the result, copies the old
file to `config.json.backup-STAMP` and writes atomically. The global options go before
the command. Guide: [projects and providers](../guide/projects-and-providers.md).

| Global option | Meaning |
| --- | --- |
| `--config PATH` | Configuration file. Default `~/.config/agent-utils/config.json`. |
| `--dry-run` | Validate and print the resulting JSON instead of writing; with `--send`, preview the bootstrap too. |

### `agents admin project`

Adds and changes project entries. Renaming or moving an entry does not rename or move
existing agterm rows.

```sh
agents admin project add --project example --name Example --window Work --agent claude --root ~/Projects/example --send
agents admin project edit --project example --root ~/Projects/example --root ~/Projects/shared
agents admin project order --project hobby --position 1
```

| Command | What it does |
| --- | --- |
| `agents admin project add` | New project. Needs `--project`, `--window` and at least one `--root`; a new window also needs `--agent` as its default provider. Fails when the id exists. |
| `agents admin project edit` | Changes `--name`, replaces the roots with the given `--root` list, sets `--agent` as the project default. |
| `agents admin project enable` | Sets `"enabled": true`. |
| `agents admin project disable` | Sets `"enabled": false`; the project leaves menus and bootstrap. |
| `agents admin project move` | Moves the entry to the existing window named by `--window`. |
| `agents admin project order` | Moves the entry to `--position` within its window. |

| Option | Meaning |
| --- | --- |
| `--project ID` | Project id. Required for every operation. |
| `--name NAME` | Display name. Default for `add`: the id. |
| `--window NAME` | Window for `add` (created when missing) or the destination for `move`. |
| `--agent AGENT` | Default provider: the window default for a new window, else the project's `default_agent`. |
| `--root PATH` | Project root, repeatable, stored as an absolute path. The first root is the working folder. |
| `--position N` | One-based place in the window's project list. Required for `order`; also accepted by the other operations. |
| `--send` | After the edit, run `agents agterm bootstrap --project ID`: create that enabled project's window and workspace without starting a model. |

### `agents admin settings`

Provider visibility, launch arguments and agent order. Without a change option it opens
the provider settings menu (toggle auto-detect and each provider, edit launch arguments).
`agents admin settings edit` is accepted as the same command.

```sh
agents admin settings [CHANGE OPTIONS]
agents admin settings --hide-agent cursor-agent
agents admin settings --auto-detect off --enabled-agent claude --enabled-agent pi
agents admin settings --agent kiro-cli --launch-args="--v3 --trust-all-tools"
agents admin settings --window Work --agent-order 'kiro-cli,claude,*'
agents admin settings --project demo --zellij-layout agent-shell --zellij-session project
```

| Option | Meaning |
| --- | --- |
| `--auto-detect on/off` | On: menus show supported executables found on PATH, minus `hidden`. Off: only the `enabled` list. |
| `--hide-agent AGENT` | Add to `providers.hidden` (repeatable). |
| `--show-agent AGENT` | Remove from `providers.hidden` (repeatable). |
| `--enabled-agent AGENT` | Replace the manual `providers.enabled` list (repeat for each; order kept). |
| `--launch-args="ARGS"` | Default arguments for `--agent`, split like a shell without running one; `--launch-args=""` clears. Needs `--agent`. |
| `--agent AGENT` | The provider that `--launch-args` applies to. |
| `--agent-order LIST` | Comma list of providers for exactly one `--window` or `--project`; `*` stands for the rest; an empty value removes the order. |
| `--zellij-layout NAME` | Zellij tab layout (`zellij.layout`) for exactly one `--window` or `--project`; empty removes it. See [agents zellij](zellij.md#project-settings). |
| `--zellij-session MODE` | `zellij.session`: `shared`, `project` or a session name; empty removes it. |
| `--window NAME` / `--project ID` | Target of `--agent-order`, `--zellij-layout` and `--zellij-session`. |

`agents admin config set providers.auto_detect|providers.hidden|providers.enabled|providers.launch_args.AGENT`
edits the same keys in the same file, `config.json`, with a diff preview and `--yes`.
Agent order has no `admin config` key.

### `agents admin settings list`

Prints the `providers` block with its defaults filled in and the resulting `visible`
provider list. It refuses change options.

```sh
agents admin settings list
```

## `agents admin inbox`

Health check, thread listing, session dispatch and state edits for the Plannotator Inbox
(`agent_utils/plannotator/`). Thread data is read from the Inbox's own files, whose layout
is undocumented; it was checked on Plannotator 0.29.0. These commands also use the flat
parser, so `--config` and `--dry-run` go before `inbox` (`agents admin --dry-run inbox
dispatch --answered`). Guide: [Inbox](../guide/inbox.md).

### `agents admin inbox doctor`

Checks the Inbox status record, that its pid lives, that the running version matches the
CLI, that only one Inbox server runs, that the page answers within 3 s and that a
`read_thread` API call answers within 15 s. Exit 0 when healthy, 1 otherwise. A version
other than 0.29.0 adds a note to recheck the file-based features.

```sh
agents admin inbox doctor [--fix [--yes]]
```

| Option | Meaning |
| --- | --- |
| `--fix` | When unhealthy, offer `plannotator inbox restart`. Previews unless `--yes`. |
| `--yes` | With `--fix`, restart and print the report before and after. |

### `agents admin inbox threads`

Lists open threads with no agent session as JSON, with the window and workspace each
would be dispatched to. Resolved threads are left out.

```sh
agents admin inbox threads [--answered] [--all] [--thread ID] [--window NAME] [--project ID]
```

| Option | Meaning |
| --- | --- |
| `--answered` | Only threads whose last message is the person's. |
| `--all` | Also threads that already have an agent session. |
| `--thread ID` | Only these threads (repeatable). |
| `--window NAME` / `--project ID` | Only threads that match this window or configured project. |

### `agents admin inbox dispatch`

Starts one agent session per selected thread in the configured project whose root matches
the thread's project (an exact root wins over a parent folder, across all windows), in
the thread's folder, titled with its subject. The first prompt tells the agent to read
the thread and reply from its own Inbox tool. Each thread is dispatched once; the record
is `~/.local/state/agent-utils/inbox.json`. Prints a JSON report of launched, planned and
skipped threads.

```sh
agents admin --dry-run inbox dispatch --answered
agents admin inbox dispatch --all --window Work --agent kiro-cli
```

| Option | Meaning |
| --- | --- |
| `--thread ID` | Dispatch these threads (repeatable). One of `--thread`, `--answered`, `--all` is required. |
| `--answered` | Threads whose last message is the person's. |
| `--all` | Every open thread without a session. |
| `--force` | Dispatch again, and include threads that already have a session. |
| `--agent AGENT` | Provider to start. Default: the project's, else the window's default. |
| `--window NAME` / `--project ID` | Skip threads that match another window or project (applied after matching). |
| `--dry-run` (global) | Print the plan; start nothing. |

### `agents admin inbox launches`

Lists sessions that Plannotator itself started from the Inbox, with the matching agterm window and workspace and whether `adopt` can attach to them.

```sh
agents admin inbox launches
```

### `agents admin inbox adopt`

Opens an agterm row in the launch's workspace that attaches to it (`claude attach REF`
or `codex resume REF`). Skips unknown, deleted or failed launches and launches with no
matching project.

```sh
agents admin inbox adopt --launch ID
```

| Option | Meaning |
| --- | --- |
| `--launch ID` | Launch id from `inbox launches` (repeatable, required). |
| `--dry-run` (global) | Report without opening rows. |

### Thread edits

`agents admin inbox rename`, `agents admin inbox subject`, `agents admin inbox reopen`,
`agents admin inbox author`, `agents admin inbox withdraw` and `agents admin inbox move`
change Inbox state that no tool or UI supports. Each needs exactly one `--thread ID` and
prints a JSON report. Each previews by default. `--yes` refuses a Plannotator version
other than 0.29.0, backs up the affected files to
`<plannotator data dir>/inbox/agent-utils-backups/`, stops the Inbox, edits, reads the
result back (restoring the originals if it does not match) and restarts the Inbox. The
global `--dry-run` keeps it a preview even with `--yes`. Exit 1 when the restarted Inbox
is unhealthy.

```sh
agents admin inbox rename --thread ID --name "Example, review" --yes
```

| Command | What it does | Options |
| --- | --- | --- |
| `agents admin inbox rename` | Sets the thread title (`thread_name`), at most 80 characters, or clears it so the subject shows. Refuses a title an open thread in the project already has. | `--name TITLE` or `--clear` |
| `agents admin inbox subject` | Sets the root message's subject, at most 200 characters. | `--subject TEXT` |
| `agents admin inbox reopen` | Reopens a resolved thread. | none |
| `agents admin inbox author` | Changes the author name and/or host of one agent message (at most 60 characters each). | `--message MSG_ID`, `--name NAME`, `--author-host HOST` |
| `agents admin inbox withdraw` | Marks an unanswered question as superseded. | `--question MSG_ID/KEY` |
| `agents admin inbox move` | Moves the thread with its questions, steps and annotations to another Inbox project; attachments keep their absolute paths. | `--destination PROJECT` (id, key, name or root of one other Inbox project) |

| Option | Meaning |
| --- | --- |
| `--thread ID` | The thread to edit. Required, exactly once. |
| `--yes` | Apply the edit. |

With `--yes` an edit stops the Inbox, backs up each touched file to
`<data-dir>/inbox/agent-utils-backups/`, writes, reads the files back, restarts the Inbox, runs
`inbox doctor` and asks the Inbox (`read_thread`) whether it shows the change. On a Plannotator version
other than 0.29.0 the edit still runs and the report carries a `version_note`. If a check fails, the
originals are written back, the Inbox is restarted again, and the command exits 1 with a message that
starts with ⚠️, says whether the Inbox came back healthy and names the backups. When the Inbox was not
running, it is left stopped and only the file read-back is checked.

### `agents admin inbox relay`

Optional wake for [Kiro](https://kiro.dev) sessions.
It types a one-line pointer to an unread person reply, plus Enter, into the agterm pane of a live Kiro session connected to the thread, once per reply, only when the session's turn marker has been absent for the idle time and the pane's last line is Kiro's empty prompt.
Needs `agtermctl` and `plannotator` (exit 1 without them).
Stop it with Ctrl-C, or run it as a launchd job through `scripts/configure-relay.py`.
Guide: [Inbox](../guide/inbox.md).

```sh
agents admin inbox relay [--once] [--interval 8] [--idle-seconds 10]
```

| Option | Meaning |
| --- | --- |
| `--once` | One pass, then exit; the idle time is not waited for. |
| `--interval SECONDS` | Seconds between passes. Default 8, minimum 2. |
| `--idle-seconds SECONDS` | How long a Kiro session must be idle first. Default 10. |
| `--dry-run` (global) | Only log what would be typed. |

## `agents admin skills`

One store of skills for every agent in `~/.agents/skills`
(`agent_utils/core/skills.py`). Codex, OpenCode, Cursor, Pi, Kimi, Qwen, Copilot and
Vibe read the store directly; Claude, Kiro and Antigravity get one relative symlink per
skill in their own folder. Installer-owned skills are left alone. Every mutating
subcommand previews until `--apply`; replaced copies go to
`~/.local/state/agent-utils/backups/<time>/<agent or store>/<skill>`. The policy is
`~/.config/agent-utils/skills.json` (backed up to `skills.json.backup-STAMP` on each
edit). A mutating run exits 1 when any item was skipped. Guide:
[skills](../guide/skills.md).

`--project DIR` works on every subcommand below, before or after the subcommand name. It
switches to project scope: `DIR/.agents/skills` as the store and `DIR/.claude/skills`,
`DIR/.kiro/skills` as linked folders. Project scope has no policy file and no imports, so
`dismiss`, `import`, `exclude` and `include` refuse it, and `reviewed` writes a marker
file in the skill instead.

```sh
agents admin skills [--project DIR] COMMAND [OPTIONS]
```

### `agents admin skills status`

Shows the store and each agent's state per skill (`ok`, `missing`, `excluded`, `managed`,
`variant`, `external`, ...), owners, and copies in store readers' own folders. Exit 1
when anything needs attention.

| Option | Meaning |
| --- | --- |
| `--project DIR` | Project scope. |
| `--json` | Print JSON. |

### `agents admin skills sync`

Adopts unmanaged copies into the store (the newest content wins; identical content
prefers the store), backs up duplicates, renames a folder to its frontmatter `name`,
creates links and relinks imports.

| Option | Meaning |
| --- | --- |
| `--project DIR` | Project scope. |
| `--apply` | Make the changes. |

### `agents admin skills link`

Only fixes links per policy: creates missing links for store skills, removes our links
where the policy excludes the agent, and (with `--apply`, user scope) removes our links
whose target is gone. A real directory in the way is skipped with a hint to use `sync`.

| Option | Meaning |
| --- | --- |
| `--project DIR` | Project scope. |
| `--apply` | Make the changes. |

### `agents admin skills add`

Copies a skill into the store (a skill already there is backed up first), records its
source in the policy and links it. Re-run to update.

```sh
agents admin skills add SRC [--name NAME] [--agents kiro,claude] [--apply]
```

| Option | Meaning |
| --- | --- |
| `SRC` | A directory with `SKILL.md`, or a git URL with an optional `#subdir`. |
| `--name NAME` | Store name. Default: the folder name. |
| `--agents LIST` | Link only for these linked agents (`claude`, `kiro`, `antigravity`); recorded in the policy. |
| `--project DIR` | Project scope. |
| `--apply` | Make the changes. |

### `agents admin skills remove`

Removes our links for a skill. `--purge` also backs up and deletes unmanaged copies, and
without `--agents` the store copy and the import.

| Option | Meaning |
| --- | --- |
| `NAME` | The skill. |
| `--agents LIST` | Only these agents. |
| `--purge` | Also back up and delete copies. |
| `--project DIR` | Project scope. |
| `--apply` | Make the changes. |

### `agents admin skills adopt`

Moves the newest copy of a skill into the store and links the rest.

```sh
agents admin skills adopt NAME [--from claude] [--apply]
agents admin skills adopt --all [--apply]
```

| Option | Meaning |
| --- | --- |
| `NAME` | The skill. Give `NAME` or `--all`. |
| `--all` | Every skill found in agent folders. |
| `--from AGENT` | Only consider this agent's copy. |
| `--project DIR` | Project scope. |
| `--apply` | Make the changes. |

### `agents admin skills check`

Read-only drift report: missing or dangling links, copies that differ from the
store or are missing from it, bad store entries, orphan lock entries, a Plannotator
version outside the checked list, skills that use Claude-only tools, imports whose source
changed, and marketplace skills to offer. Exit 1 when there are findings other than
offers, except with `--auto` or `--inbox`, where only tool errors give a non-zero exit.
Runs lock against overlap. Tests use fake agents and a fake Inbox.

| Option | Meaning |
| --- | --- |
| `--project DIR` | Check only this project. |
| `--also-project DIR` | Also check this project (repeatable). |
| `--json` | Print JSON. |
| `--all-markets` | Also offer skills from marketplaces whose skills rely on their own agent's runtime. |
| `--agent AGENT` | An agent reviews the findings read-only and proposes `link`, `adopt`, `exclude`, `remove-link` or `ask-owner`; `ask` picks the agent from a menu. |
| `--no-agent` | Basic check only, no agent (the default); conflicts with `--agent`. |
| `--auto` | Apply the safe actions: create missing links, remove our dangling links. |
| `--inbox` | Post one question per finding to the Inbox thread "Skills drift"; a later run applies the answers. |

### `agents admin skills import`

Copies a marketplace skill (Claude, Codex or Cursor plugin, Gemini extension) to
`~/.local/share/agent-utils/skills/NAME` with a source record and links it into the own
folder of each installed agent that lacks it. User scope only.

| Option | Meaning |
| --- | --- |
| `SRC` | The marketplace skill folder (with `SKILL.md`). |
| `--name NAME` | Name. Default: the frontmatter `name`, else the folder name. |
| `--agents LIST` | Only these agents. Default: every installed agent without it. |
| `--project DIR` | Refused (user scope only). |
| `--apply` | Make the changes. |

### `agents admin skills dismiss`

Stops `check` offering these marketplace skills. Writes `dismissed` in the policy.

| Option | Meaning |
| --- | --- |
| `NAME...` | One or more skill names. |
| `--project DIR` | Refused (user scope only). |
| `--apply` | Make the change. |

### `agents admin skills reviewed`

Records the current content of a store skill or import as adapted, so `check` stops
listing it until the content changes.

| Option | Meaning |
| --- | --- |
| `NAME` | The skill. |
| `--project DIR` | Project scope: writes a marker file in the skill. |
| `--apply` | Make the change. |

### `agents admin skills exclude`

Excludes one linked agent from a skill in the policy. Links follow on the next `link` or
`sync`.

| Option | Meaning |
| --- | --- |
| `NAME` | The skill. |
| `--agent AGENT` | Required: `claude`, `kiro` (`kiro-cli`) or `antigravity` (`agy`). Store readers cannot be excluded. |
| `--project DIR` | Refused (user scope only). |
| `--apply` | Make the change. |

### `agents admin skills include`

Undoes `exclude` for one linked agent. Same options as `exclude`: `NAME`, `--agent`
(required), `--project`, `--apply`.

### `agents admin skills audit`

Read-only audit of one project's AGENTS.md and agent-specific rule files
(`agent_utils/core/audit.py`): skill-shaped sections with a draft name and pointer line,
each rule file compared with AGENTS.md (`duplicate`, `overlaps`, `differs`, `own`,
`imports`), project skill copies outside `.agents/skills`, and root skills an agent
started in a child repository misses (from the Claude Code, Codex and Kiro docs). The report goes to
`~/.local/state/agent-utils/audits/<project>-<hash>/<time>/report.md` and `.json`.

```sh
agents admin skills audit PROJECT [--json] [--agent codex] [--prompt] [--timeout 900] [--inbox]
```

| Option | Meaning |
| --- | --- |
| `PROJECT` | Project folder. |
| `--json` | Print the report as JSON. |
| `--agent AGENTS` | Read-only `agents agent ask` proposals from these agents (`claude`, `codex`, `kiro`, `a,b` or `all`). |
| `--prompt` | Print the agent prompt instead of asking an agent; writes no report. |
| `--timeout SECONDS` | Agent time limit. Default 900. |
| `--inbox` | Post one Inbox thread with a question per finding. |

### `agents admin skills extract`

Writes a skill draft from one section to `PROJECT/.agents/skills/NAME/SKILL.md`, links it
into `.claude/skills` and `.kiro/skills`, and writes a patch that replaces the section
with a pointer line. The patch is not applied; review it, then `git -C PROJECT apply
PATCH`. Previews unless `--apply`.

```sh
agents admin skills extract PROJECT --section L42 --name NAME [--description TEXT] [--file AGENTS.md] [--apply]
```

| Option | Meaning |
| --- | --- |
| `PROJECT` | Project folder. |
| `--section TEXT` | Required: heading text or `L<line>`. |
| `--name NAME` | Required: skill name. |
| `--description TEXT` | Skill description. |
| `--file PATH` | Source file, project-relative. Default `AGENTS.md`. |
| `--apply` | Write the skill, the links and the patch. |

### `agents admin skills prune-rules`

Removes lines of agent-specific rule files that AGENTS.md already has (and fenced blocks
whose lines are all duplicates), drops headings left empty and deletes a file with
nothing left. Every changed file is backed up to
`~/.local/state/agent-utils/backups/<time>/rules/`. Previews the diff unless `--apply`.

```sh
agents admin skills prune-rules PROJECT [FILE...] [--apply]
```

| Option | Meaning |
| --- | --- |
| `PROJECT` | Project folder. |
| `FILE...` | Only these project-relative files. Default: all, except a CLAUDE.md that imports AGENTS.md. |
| `--apply` | Change the files. |

### `agents admin skills schedule`

Runs `agents admin skills check` from launchd (`agent_utils/core/skillcheck.py`).
The job is `~/Library/LaunchAgents/agent-utils.skills-check.plist`, logging to `~/.local/state/agent-utils/skills-check.log`.

### `agents admin skills schedule install`

Prints the job; `--apply` writes it and loads it with `launchctl bootstrap` (replacing a
loaded one).

```sh
agents admin skills schedule install [--every 1d] [--agent codex] [--auto] [--inbox] [--project DIR] [--apply]
```

| Option | Meaning |
| --- | --- |
| `--every INTERVAL` | `30m`, `12h`, `1d`, `1w`; at least 15m. Default `1d`. |
| `--agent AGENT` | Fixed reviewing agent (`ask` is refused: a scheduled run cannot ask). |
| `--auto` | Pass `--auto` to the check. |
| `--inbox` | Pass `--inbox` to the check. |
| `--project DIR` | Also check this project (repeatable; passed as `--also-project`). |
| `--apply` | Write and load the job. |

### `agents admin skills schedule remove`

Unloads and deletes the job. Previews unless `--apply`.

| Option | Meaning |
| --- | --- |
| `--apply` | Remove the job. |

## `agents admin instructions`

One rule set for every installed agent (`agent_utils/core/instructions.py`). Sources
are in `~/.config/agent-utils/instructions/`: `common.md` for every agent plus optional
parts `claude.md`, `kiro.md`, `codex.md`, `pi.md`, `opencode.md`, `cursor.md`. Claude,
Kiro, Copilot, Kimi, Qwen, Vibe and Antigravity get symlinks, OpenCode gets paths in
`opencode.json`, and Codex, Pi and Cursor get a copy between markers, so they need a
rerun after a source edit. Agents whose executable is not found are skipped. Guide:
[instructions](../guide/instructions.md).

```sh
agents admin instructions [status|apply|remove] [--yes] [--force] [--json]
```

| Option | Meaning |
| --- | --- |
| `status` | Default. Per-agent state: `ok`, `stale` (copy out of date), `edited` (copy edited by hand), `foreign` (file not created here), and missing. Exit 1 unless every target is `ok`. |
| `apply` | Link or write every target. The first apply writes a starter `common.md` from the default snippets when none exists. Previews unless `--yes`. |
| `remove` | Remove the links and copies; the sources stay. Previews unless `--yes`. |
| `--yes` | With `apply` or `remove`, make the changes. |
| `--force` | Replace a `foreign` file or an `edited` copy; a backup is written beside the file first. |
| `--json` | Print JSON. |

`apply` and `remove` exit 1 when a target was skipped. Text outside the markers is never
touched. Changes load in each agent's next session.

### `agents admin instructions compose`

Builds an instruction file from snippets (`agent_utils/core/snippets.py`), each copied
between markers with a checksum. Bundled snippets are in the package; private ones in
`~/.config/agent-utils/snippets/` win on the same id. `{{name}}` values come from
`~/.config/agent-utils/snippets.json`. Without a change option it prints the state of
each snippet: `ok`, `stale`, `edited`, `missing`, `unset`. A change previews a diff;
`--yes` backs up the file to `FILE.backup-STAMP`, writes it and, for a shared source,
runs `agents admin instructions apply` so every installed agent gets it. Exit 1 when a
snippet was skipped.

```sh
agents admin instructions compose --list
agents admin instructions compose --add untrusted-content --yes
agents admin instructions compose --part codex --add commit-conventions --yes
agents admin instructions compose --project ~/Projects/example --add privacy-in-repos --yes
```

| Option | Meaning |
| --- | --- |
| `--target PATH` | Edit this instruction file. No sync. |
| `--project DIR` | Edit `DIR/AGENTS.md`. No sync. |
| `--part PART` | Edit one shared source part: `common`, `claude`, `kiro`, `codex`, `pi`, `opencode`, `cursor`. Default `common`. |
| `--list` | List library snippets with their default state and the agents they apply to. |
| `--status` | Show each snippet's state in the target (the default action). |
| `--add ID` | Add a snippet (repeatable). |
| `--remove ID` | Remove a snippet (repeatable). |
| `--defaults` | Add every snippet marked `default: on`. |
| `--replace` | The file becomes exactly the added snippets, in order; other text is dropped (backup kept). |
| `--no-sync` | After writing a shared source, do not run `agents admin instructions apply`. |
| `--update` | Refresh stale copies. |
| `--force` | Overwrite or remove hand-edited copies. |
| `--yes` | Write the change. |
| `--json` | Print JSON. |

## `agents admin notify`

Push notifications for agent events (`agent_utils/core/notify.py`).
Off by default; the only transport is an ntfy URL.
Config: `~/.config/agent-utils/notify.json`.
The same parser is reachable as `agents agent notify ...` (see [agent.md](agent.md)); both routes accept every subcommand.
Agents and hooks normally use the agent route for `send` and `hook`, people use this route for the rest.
The changing subcommands (`enable`, `disable`, `url`, `template`) write at once, without a backup; `agents admin config set notify.*` makes the same change with a preview and a backup.
Guide: [notifications](../guide/notifications.md).

```sh
agents admin notify [--dry-run] COMMAND ...
```

| Option | Meaning |
| --- | --- |
| `--dry-run` | Goes before the subcommand. `send`, `hook`, `test`: print what would be sent. Changes: print the file that would be written. |

### `agents admin notify send`

Renders the title and body templates and posts one event. A disabled or unreachable
setup prints nothing or one line and exits 0, so a hook never fails.

```sh
agents admin notify send --agent NAME --event done|input|failed|info [--session NAME] [--detail TEXT] [--quiet]
```

| Option | Meaning |
| --- | --- |
| `--agent NAME` | Required: the agent name shown as `{agent}`. |
| `--event EVENT` | Required: `done`, `input`, `failed` or `info`. |
| `--session NAME` | Default: `AGENT_UTILS_SESSION_NAME`, then the agterm sidebar name, then the folder name. |
| `--detail TEXT` | What finished or what is needed. |
| `--quiet` | Print nothing. |

### `agents admin notify hook`

Adapter for an agent's own hook payload. Claude: reads the hook JSON on stdin; `Stop`
becomes `done`, `Notification` becomes `input`. Codex: takes its JSON as an argument;
`agent-turn-complete` becomes `done`. Other payloads send nothing. Always exits 0.

```sh
agents admin notify hook claude < payload.json
agents admin notify hook codex '{"type": "agent-turn-complete"}'
```

| Option | Meaning |
| --- | --- |
| `claude` / `codex` | The agent whose payload format is read. |
| `PAYLOAD` | Optional JSON argument (Codex); without it stdin is read. |

### `agents admin notify show`

Prints the configuration, the file path and the placeholders `{agent} {session} {event}
{event_text} {detail} {detail_suffix}`.

### `agents admin notify enable`

Turns notifications on. Fails until a URL is set.

### `agents admin notify disable`

Turns notifications off.

### `agents admin notify test`

Sends one `info` event now, even when notifications are disabled. Exit 1 when it was not
sent.

### `agents admin notify url`

Sets the ntfy URL.

```sh
agents admin notify url https://example.invalid/YOUR-TOPIC
```

| Option | Meaning |
| --- | --- |
| `URL` | `http(s)://host/topic`. |

### `agents admin notify template`

Sets the title and/or body template; unknown placeholders stay as written.

```sh
agents admin notify template --title '{session}' --body '{event_text}{detail_suffix}'
```

| Option | Meaning |
| --- | --- |
| `--title TEMPLATE` | Title template. Default `{session}`. |
| `--body TEMPLATE` | Body template. Default `{event_text}{detail_suffix}`. |

`agents admin notify away-after SECONDS` sets `away_after_seconds` (0 to 86400, default 120).
On macOS `send` and `hook` push nothing while the last keyboard or mouse input (read from
`ioreg`, `HIDIdleTime`, any app) is newer than that; 0 turns the check off, `test` ignores it,
and an unreadable idle time never suppresses a push. `--dry-run send` prints `would skip: at the
computer` in that case.

`agents admin notify away-when-locked on|off` sets `away_when_locked` (default on): a locked screen
(`ioreg`, `IOConsoleLocked`) counts as away, so the push is sent even if the last input was recent.

`agents admin notify mobile-host HOST` sets the saved Mac hostname (or saved Mac UUID)
used by the mobile app. When it is set and AGTERM_SESSION_ID is available, ntfy messages
include an `agterm-mobile://HOST/session/ID` Click URL. Pass an empty string to disable.
The hostname must match a Mac saved in agterm mobile. Existing notification setups are
unchanged until this is configured.
