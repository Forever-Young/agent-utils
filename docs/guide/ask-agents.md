# Ask other agents for a second opinion

`agents agent ask` runs other installed agents once, non-interactively and read-only,
in parallel, and collects their answers. It is meant for agents (through the
`ask-agents` skill), but you can run it yourself.

```sh
agents agent ask check                                   # verify every adapter against its CLI help
agents agent ask codex "Why does test_x fail? See src/x.py:40"
agents agent ask claude,kiro --diff staged "Review this change"
agents agent ask all --file notes.md "..."               # every installed agent except the caller
agents agent ask codex --target-args codex="-c model_reasoning_effort=xhigh" - < prompt.md
```

Output is one JSON line: `run`, the folder under `~/.local/state/agent-utils/ask/`
with the answers, and one result per target with `status` (`ok`, `failed`, `timeout`,
`too-large`, `empty`, `unsupported`, `missing`, `error`), the `answer` file,
`exit_code`, `seconds` and, on failure, `stderr_tail`. `--text` also prints the
answers. The exit status is 0 only when every target answered.

## How it stays read-only

Each target runs in its documented read-only or plan mode (the flags per agent are in [agent.md](../reference/agent.md#agents-agent-ask)).
Before a run, the target's own help must list every flag its adapter uses; otherwise it is `unsupported` and does not start.
Only that flag check is automated; the read-only behavior is each CLI's own mode.

Guards:

- `--file` and `--diff` refuse secret-looking paths (`.env*`, keys, `*.pem`,
  credentials, `auth.json`) and private key blocks.
- Embedded context is capped at 200 KB and each answer at 1 MB.
- `--timeout` (default 900 s) kills the target's process group.
- A nested ask is refused (`AGENT_UTILS_ASK_DEPTH`, limit `AGENT_UTILS_ASK_MAX_DEPTH`,
  default 1).
- Targets get no `AGTERM_*` or agent session variables, so their hooks cannot rename or
  mark your agterm row.

The prompt starts with a short preamble: the agent is consulted read-only, should not
ask others, and gets the paths of instruction files it would not load by itself.
There is no write-capable task mode.

## Skills

Install with `agents admin skills add skills/ask-agents --apply` (and the same for
`skills/agent-history`):

- [ask-agents](../../skills/ask-agents/SKILL.md) tells an agent when and how to ask.
- [agent-history](../../skills/agent-history/SKILL.md) reads what other agents already
  know, offline: Claude Desktop cached chats and project instructions, Claude Code rules
  and project memory, Codex conversations and rules.
