# Tasks, workers and the coordinator

A **task** session is an agent started for one piece of work (a ticket, a branch) in
its own git worktree. A **worker** is such a session that another agent, the
**coordinator**, sends follow-ups to. agent-utils provides the launch, lookup, message
delivery and result plumbing. It has no queue, scheduler or autonomous loop: review and
orchestration policy belongs to the agents' own skills.

## Start a task

```sh
agents agterm task --project example --task issue-123 \
  --description 'Fix parser' --prompt 'Implement issue-123 using project rules'
```

- One branch and worktree per project root, named after `--task`. `--repo` (repeatable)
  limits it to some roots, `--destination` sets the parent folder (default: the state
  dir's `worktrees/`), `--base` the base revision (default `HEAD`).
- The agent starts in the worktrees; the project configuration is unchanged.
- A dirty checkout, an existing branch or an existing target folder is refused. When one
  of several repositories fails, the finished worktrees stay for you to inspect.
- `--in-place` skips worktrees and runs in the project roots.

Ordinary `agents agterm new` never creates worktrees. `agents agterm worktree --task
NAME --repo PATH --destination DIR` creates worktrees without starting an agent.

## Find a worker

```sh
agents agterm session list --project example --task ISSUE-123
agents agterm session list --project example --pr https://example.invalid/team/app/pull/42
agents agterm session list --project example --branch feature/parser --compact --refresh
```

Filters (`--task` with its aliases, `--conversation`, `--pr`, `--branch`, `--repo`,
`--record`) combine with AND. An exact lookup includes hidden and archived records,
refreshes liveness and prints one compact JSON array. `--live` keeps only sessions whose
agent process is verified running.

Add links to a worker:

```sh
agents agterm task bind --record RECORD_ID --pr https://example.invalid/team/app/pull/42 \
  --branch feature/parser --alias provisional-parser --repo ~/Projects/example
agents agterm task rename --record RECORD_ID --to-task ISSUE-123   # old key stays as an alias
```

Repo links are declared metadata, not a record of which files an agent edited.

## Send a follow-up (`ensure`)

```sh
agents agterm ensure --project example --window Work --agent claude \
  --task ISSUE-123 --prompt 'Address the review comments'
```

`ensure` works for Claude, Codex and [Kiro](https://kiro.dev) workers (`--agent codex`, for example, when a task fits it better or Claude is out of limits).
Other agents can only be **created**, with the prompt as the first message.
Follow-ups to a live Codex or Kiro worker are typed through `agtermctl`; they have no provider inventory, so the check is the runner's tracked process plus the terminal foreground being that agent.
A stopped Codex or Kiro worker is not resumed by `ensure`; resume it yourself.
If the task already has a worker on another agent, `ensure` refuses rather than start a second writer: finish or archive the old one first.
It finds the one worker for the identity you give (`--task`, `--pr`, `--branch`, `--conversation` or `--record`) and:

| Worker state | Action |
| --- | --- |
| live, attached and waiting | stores the full request in `~/.local/state/agent-utils/messages/` and types one short prompt pointing to it (`reused`) |
| no live process owns its conversation | resumes that exact conversation in a new row, same record (`resumed`) |
| no worker | creates one with a new conversation id (`created`) |

It prints one JSON object with `action`, ids, `state`, `alive` and `delivery`.
`delivery: submitted` means the prompt was typed, not that the worker read it or
finished. Before typing, it rechecks the process and foreground owner; this is not
atomic with a person typing, so do not type into the worker at the same time.
Permission prompts, detached or uncertain workers, a worker with no bound conversation,
and conflicting matches fail with a message instead of guessing. Resuming needs a
successful `claude agents --json` inventory. Launches and `ensure` are serialized, so
two callers do not create duplicates. Plain `new --task KEY` refuses a task that already
has a worker and points to `ensure`.

## Record a result

```sh
agents agterm session finish --record RECORD_ID --result 'Implemented; tests passed'
agents agterm session finish --record RECORD_ID --result tasks/issue-123.md --failed
agents agterm session status --record RECORD_ID --state blocked
```

`finish` saves the result and marks the record completed (or failed), then notifies the
worker's row and the project's open coordinator. An exit code of 0 or an idle agent is
not proof that a task succeeded; only an explicit result is. `status` sets the saved
state by hand (`idle running active blocked completed failed`).

In `session list` and `ensure` output, `state` is a fresh observation (`running`,
`idle`, `blocked`, `dead`, `unknown`, or `completed`/`failed` after the process
exited) and `status` is the saved lifecycle value. `alive: null` means unknown.

## The coordinator

```sh
agents agterm coordinator --project example
```

Or choose an agent in the [session chooser](sessions.md), then **Coordinator mode**
(listed after **New session**, only for Claude, Codex and Kiro). `--agent` picks the agent
for the command; the default is Claude. A project has one coordinator; to switch its
agent, forget the old one first.
The first launch creates a row named **Main** (`coordinator_name` in the config) running Claude Sonnet with
a dispatcher prompt. Later launches focus that row, or resume its saved conversation
when the row is gone. If the conversation still runs somewhere else, it refuses rather
than start a second writer. A running coordinator keeps its model, prompt and tool
gate until restarted.

The coordinator dispatches; it does not do the work itself. A tool gate (Claude
`PreToolUse`, coordinator only) denies Skill, Agent, Grep/Glob, web and non-Plannotator
MCP tools; reads outside `AGENTS.md`, `CLAUDE.md`, `tasks/` and `tmp/`; edits outside
`tasks/`; and shell commands other than `agents`, `agtermctl`, `ps`, `ls`, read-only
`git`, `cat`/`head`/`tail` on `tasks/` and `tmp/`, and `pbcopy`/`pbpaste` (with stdin
from `tasks/` or `tmp/` files, or `echo`/`printf` text). Pipelines, chaining and
redirects are denied. Each denial tells it to dispatch to a worker. The prompt puts
opening and reusing worker sessions first and forbids doing any work itself.

| Agent | Prompt | Tool gate | Resume |
|---|---|---|---|
| Claude | `--append-system-prompt` | `--settings` PreToolUse hook | exact id (`--session-id`) |
| Codex | `-c developer_instructions` | per-launch `-c hooks.PreToolUse`; **Codex asks you to review this hook on the first launch, approve it once**. Until approved the gate does not run | provider picker (no id is pre-assigned) |
| Kiro | generated custom agent `agterm-coordinator` in `~/.kiro/agents/` (rewritten on launch) with a native shell allowlist | the agent's `preToolUse` hook (exit 2 blocks); live-checked | provider picker |

Codex and Kiro rows are focused when they still exist. When the row is gone their
saved coordinator is resumed through the provider's own picker, because no
conversation id can be assigned in advance and there is no live-process check for
them; pick the right conversation yourself.

Choosing the coordinator from the startup chooser closes the temporary chooser shell
after a successful handoff; a failed one leaves the shell open.
