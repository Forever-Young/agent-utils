# agents agent

Commands that agents and their hooks call: one session name for agterm and the agent
(`rename`, `sync`), read-only consults of other agents (`ask`), and push notifications
(`notify`). They run anywhere; the agterm parts are skipped outside an agterm session.
Errors print `Error: ...` on stderr and exit 1; a wrong option exits 2.

## Session names

Code: `agent_utils/agterm/names.py`. Walk-through: [session names guide](../guide/session-names.md).

### `agents agent rename`

Renames the agterm session (`AGTERM_SESSION_ID`) and, where the agent has a route, the
agent's own session, the name shown in its resume picker. The agent and its session id
are detected from the agent's environment variables unless you pass them.

```sh
agents agent rename "Example parser, implementation"
agents agent rename "Example parser, review" --agent codex --session THREAD_ID
```

| Option | Meaning |
|---|---|
| `NAME` | The new name. Whitespace is collapsed. At most 100 characters, no double quotes or control characters. |
| `--agent AGENT` | Agent to rename, for example `codex`, `claude`, `qwen`, `pi`, `opencode`, `kiro-cli`. Default: detected. |
| `--session ID` | The agent's session id. Default: detected from the agent's variable when `--agent` matches the detected agent. |

It prints one line per target (`agterm`, then the agent) with `ok`, `pending: ...`,
`skipped: ...` or `failed: ...`. Exit status is 1 when any target failed, else 0.
Outside agterm the agterm line is `skipped: not inside agterm`.

What is set where:

| Agent | Own session name | Agent name copied to agterm |
|---|---|---|
| Codex | set now (app-server `thread/name/set`) | on start/resume and each prompt (hooks) |
| Claude, Qwen | set at the next prompt (`UserPromptSubmit` hook returns `sessionTitle`) | on start/resume (`session_title`) and each prompt |
| Pi | set after the tool call (agent-utils Pi extension) | on start/resume and on `/name` (extension) |
| OpenCode | set now (`opencode api session.update`) | no |
| [Kiro](https://kiro.dev) | not set; the command prints the title to pass to Kiro's own `update_session_information` tool | on start and each prompt (hooks read `session.json`) |
| Copilot, Cursor, Kimi, Vibe, Antigravity | not set; agterm only | no |

Claude, Qwen and Pi need the hooks or extension from [`configure-session-names.py`](setup.md#agent_utilscoresetupsession_names); without them the session id is unknown and the agent line is `skipped`.
Claude transcripts are never edited.

### `agents agent sync`

Copies a name to the agterm session only, even when it equals the last copied name.
Without `--name` it reads the agent's saved name (Codex: `session_index.jsonl` under
`CODEX_HOME` or `~/.codex`; Kiro: `~/.kiro/sessions/*/ID/session.json`).

```sh
agents agent sync --name "Example parser, implementation"
agents agent sync --agent codex --session THREAD_ID
```

| Option | Meaning |
|---|---|
| `--agent AGENT` | Agent whose saved name to read. Default: detected. |
| `--session ID` | The agent's session id. Default: detected. |
| `--name NAME` | Name to copy. Default: the agent's saved name (Codex and Kiro only). |

Exit status: 1 when no name was found or the agterm rename failed; 0 on success or
outside agterm (`skipped: not inside agterm`).

## Ask other agents

Code: `agent_utils/core/ask.py`. Walk-through: [ask-agents guide](../guide/ask-agents.md).

### `agents agent ask`

Runs each target agent once, non-interactively, in its read-only mode, in parallel,
and collects the answers. Only this review mode exists; a write-capable task mode is
not implemented.

```sh
agents agent ask codex "Why does test_x fail? See src/x.py:40"
agents agent ask claude,kiro --diff staged "Review this change"
agents agent ask all --file notes.md --text "Is this plan sound?"
agents agent ask codex --target-args codex="-c model_reasoning_effort=xhigh" - < prompt.md
```

| Option | Meaning |
|---|---|
| `TARGETS` | Comma-separated agents, or `all`: every installed agent except the caller. Names: `claude`, `codex`, `kiro-cli`, `opencode`, `cursor-agent`, `pi`, `agy`, `kimi`, `qwen`, `copilot`, `vibe`. Aliases: `kiro`, `cursor`, `antigravity`, `gemini` (= `agy`). |
| `PROMPT` | The request. `-` or omitted reads it from stdin. An empty prompt is an error. |
| `--cwd DIR` | Directory the targets work in. Default: the current directory. |
| `--file PATH` | Embed a file (relative to `--cwd`) in the prompt. Repeatable. |
| `--diff WHAT` | Embed a git diff: `unstaged`, `staged`, or a ref passed to `git diff`. |
| `--timeout SECONDS` | Time limit per target. Default 900. The target's whole process group is killed. |
| `--target-args TARGET="ARGS"` | Extra CLI arguments for one target, split like a shell line and inserted before the prompt. Repeatable. |
| `--text` | After the JSON line, also print each answer under a `===== TARGET (STATUS) =====` header. |

Output: stdout is one JSON line with `run` (a folder under
`~/.local/state/agent-utils/ask/`), `cwd`, `caller` and `results`, one per target. A
result has `target`, `status`, the `answer` file, `exit_code`, `seconds`, `bytes`,
`truncated` and, when the status is not `ok`, `stderr_tail`. The run folder holds
`prompt.md` (prompt plus embedded context), `TARGET.md` (the answer) and `TARGET.log`
(stderr).

| Status | Meaning |
|---|---|
| `ok` | The target exited 0 with a non-empty answer. |
| `failed` | The target exited non-zero. |
| `timeout` | The target ran past `--timeout` and was killed. |
| `too-large` | The target's output passed twice the 1 MB answer cap and was killed. |
| `empty` | The target exited 0 with no answer text. |
| `unsupported` | The target's help lacks a flag its adapter needs (`missing_flags`); it was not started. |
| `missing` | The target's executable was not found. |
| `error` | Starting or running the target raised an OS error (`error` holds the message). |

Exit status: 0 only when every target is `ok`, else 1.

Guards:

- `--file` and `--diff` refuse secret-looking paths (`.env*`, `.envrc`, `.netrc`,
  `.npmrc`, `.pypirc`, `id_rsa*` and other SSH keys, `*.pem`, `*.key`, `*.p8`, `*.p12`,
  `*.pfx`, keystores, `credentials*`, `secret.*`, `auth.json`) and any text with a
  private key block.
- Embedded context is capped at 200 KB; each answer is cut at 1 MB (`truncated`).
- `AGENT_UTILS_ASK_DEPTH` (set to depth + 1 for children) refuses a nested ask when it
  reaches `AGENT_UTILS_ASK_MAX_DEPTH` (default 1). See [files.md](files.md#environment-variables).
- Children get no `AGTERM_*` variables and no agent session variables, so their hooks
  cannot rename or mark the caller's agterm row.
- The prompt starts with a preamble: the agent is consulted read-only, must not ask
  other agents, and gets the absolute paths of instruction files it does not load
  itself (`AGENTS.md`, `CLAUDE.md`, `.claude/rules`, `.kiro/steering`, `GEMINI.md`,
  `QWEN.md`, `.cursor/rules`, `.github/copilot-instructions.md` in `--cwd` and its git
  root, plus the caller's global ones).

Read-only invocation per agent (from `ADAPTERS` in `ask.py`; the prompt comes last):

| Agent | Arguments |
|---|---|
| Claude | `--print --output-format text --tools Read,Grep,Glob --permission-mode default --permission-prompts none --strict-mcp-config --no-session-persistence` |
| Codex | `exec --sandbox read-only --skip-git-repo-check --ephemeral --color never --cd DIR --output-last-message ANSWER` |
| Kiro | `chat --no-interactive --trust-tools=fs_read,grep,glob,code --trust-workspace` |
| OpenCode | `run --agent plan --archived` |
| Cursor | `--print --mode ask --output-format text --workspace DIR` |
| Pi | `--print --no-session --no-mcp --tools read,grep,find,ls` |
| Antigravity | `--mode plan --sandbox --print` |
| Kimi | `--plan --prompt` |
| Qwen | `--print --permission-mode default --no-session-persistence --disallowed-tools=edit --disallowed-tools=write_file --disallowed-tools=run_shell_command` |
| Copilot | `--silent --no-ask-user --deny-tool=write --prompt` |
| Vibe | `--workdir DIR --trust --output text --enabled-tools read_file --enabled-tools grep --prompt` |

Only the flag check is automated; the read-only behavior is each CLI's documented mode.

### `agents agent ask check`

Runs each target's own help (Codex `exec --help`, Kiro `chat --help`, OpenCode
`run --help`, others `--help`) and reports whether it lists every flag its adapter
uses. It asks nothing.

```sh
agents agent ask check
agents agent ask check codex,kiro
```

| Option | Meaning |
|---|---|
| `TARGETS` | Comma-separated agents, or `all` (default): every supported agent. |

Each line shows the agent, `ok`, `unsupported` (with the missing flags) or `missing`
(not installed), and the executable. Exit status is 1 when any target is
`unsupported`, else 0.

## Notifications

Code: `agent_utils/core/notify.py`. Walk-through: [notifications guide](../guide/notifications.md).
`agents agent notify` and `agents admin notify` are the same parser; this page covers
the two subcommands that agents and hooks call. The configuration lives in
`~/.config/agent-utils/notify.json` and is off by default; the only transport is an
ntfy URL.

```sh
agents agent notify [--dry-run] SUBCOMMAND ...
```

| Option | Meaning |
|---|---|
| `--dry-run` | Goes before the subcommand. `send`, `hook` and `test` print `would send to URL: TITLE \| BODY \| priority N` instead of posting; the configuration subcommands print the file they would write. |

### `agents agent notify send`

Renders a title and body from the templates and posts them to the ntfy URL (JSON
publish: `topic` from the URL path, `title`, `message`, `priority`, `tags` = the
event). It always exits 0, so a hook never fails because of it.

```sh
agents agent notify send --agent codex --event done --detail "tests pass"
agents agent notify send --agent kiro-cli --session "Example parser" --event input --quiet
```

| Option | Meaning |
|---|---|
| `--agent NAME` | Required. The agent sending the event; fills `{agent}`. |
| `--session NAME` | Session name for `{session}`. Default: `AGENT_UTILS_SESSION_NAME`, then the agterm sidebar name of `AGTERM_SESSION_ID` (from `agtermctl tree --json`), then the current folder name. |
| `--event EVENT` | Required. One of `done` (text "Done", priority 3), `input` ("Input needed", 4), `failed` ("Failed", 4), `info` ("Info", 3). |
| `--detail TEXT` | What finished or what is needed. Default: empty. |
| `--quiet` | Print nothing. Without it, `send` prints one line only when it did not send for a reason other than being disabled (`no valid url configured`, `send failed: ...`). |

Placeholders in the title and body templates: `{agent}`, `{session}`, `{event}`,
`{event_text}` (the text above), `{detail}`, `{detail_suffix}` (`: DETAIL`, or empty
without a detail). Unknown placeholders are left as written. Defaults: title
`{session}`, body `{event_text}{detail_suffix}`. A title that renders empty falls back
to the session or agent name.

### `agents agent notify hook`

Adapter for an agent's own hook payload: maps it to an event and sends it like `send`,
with the default session name. Payloads that map to no event are ignored. It always
exits 0 and has no `--quiet`.

```sh
agents agent notify hook claude              # Claude hook JSON on stdin
agents agent notify hook codex '<json>'      # Codex notify passes the JSON as an argument
```

| Option | Meaning |
|---|---|
| `AGENT` | `claude` or `codex`. |
| `PAYLOAD` | The hook JSON. Default: read from stdin (Claude). |

| Agent | Payload | Event |
|---|---|---|
| `claude` | `hook_event_name` `Stop` | `done` |
| `claude` | `hook_event_name` `Notification` | `input`, detail = first 200 characters of `message` |
| `codex` | `type` `agent-turn-complete` | `done` |

The Kiro hooks from `configure-kiro.py` call `send` with `input` on permission requests and `done` on stop.
Claude Code and Codex hooks are not changed by agent-utils.

### Configuration subcommands

These manage `notify.json`; [admin.md](admin.md) documents them under `agents admin notify`.

| Command | What it does |
|---|---|
| `agents agent notify show` | Print the configuration, its file and the placeholders. |
| `agents agent notify enable` | Turn notifications on; needs a URL first. |
| `agents agent notify disable` | Turn notifications off. |
| `agents agent notify test` | Send one `info` test event now, even when disabled. |
| `agents agent notify url URL` | Set the ntfy URL, `https://ntfy.sh/YOUR-TOPIC`. |
| `agents agent notify template` | Set the title (`--title`) and/or body (`--body`) template. |


`agents agent notify mobile-host HOST` sets the saved Mac hostname (or saved Mac UUID)
used by the mobile app. When it is set and AGTERM_SESSION_ID is available, ntfy messages
include an `agterm-mobile://HOST/session/ID` Click URL. Pass an empty string to disable.
The hostname must match a Mac saved in agterm mobile. Existing notification setups are
unchanged until this is configured.
