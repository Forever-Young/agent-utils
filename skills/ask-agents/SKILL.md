---
name: ask-agents
description: Ask another coding agent (Claude, Codex, Kiro, OpenCode, Cursor, Pi, Antigravity, Kimi, Qwen, Copilot, Vibe) for a read-only second opinion on an investigation, a bug or a code review, through `agents agent ask`. Use when the user says "ask codex", "ask claude", "ask kiro", "check with codex", "codex review", "get a second opinion", "ask all agents", or as a last resort after 4+ failed attempts at the same bug or investigation with no ideas left. Asked agents are slow (2-10 minutes) and only analyze; you implement.
---

# Ask agents

`agents agent ask` runs other agents non-interactively in their read-only mode and returns
their answers. Use it for a second opinion, not to hand off work.

## When

- Explicit: "ask codex/claude/kiro/...", "check with X", "X review", "what does X
  think", "second opinion", "ask all agents".
- Stuck: 4+ failed attempts at the same fix or investigation, every reasonable
  approach tried, no progress. Say that you are asking and why.

Not for questions you can answer yourself, file searches, or work whose solution is
already clear.

## Steps

1. Check: `agents agent ask check TARGET` prints `ok`, `missing` (not installed) or
   `unsupported` (its CLI lacks a flag the read-only adapter needs). Stop and tell
   the user if the target is not `ok`.
2. Build a focused prompt from the conversation: the problem in 2-3 sentences, what
   you know (paths with line numbers, errors, observed and expected behavior), what
   you tried and why it failed, and one specific question. Give paths, not file
   contents: the asked agent reads the project itself. Embed a file with `--file F`
   or a change with `--diff unstaged|staged|REF` only when the agent cannot see it
   otherwise (for example uncommitted work it might not inspect).
3. Run it. It takes minutes; run it in the background or with a timeout of at least
   15 minutes if your environment allows, and do not poll faster than every 30 s:

   ```sh
   agents agent ask codex "PROMPT"                 # one agent
   agents agent ask claude,codex --diff staged "PROMPT"   # several, in parallel
   agents agent ask all "PROMPT"                   # every installed agent except you
   agents agent ask codex --target-args codex="-c model_reasoning_effort=xhigh" "PROMPT"
   ```

   A long prompt can come from a file: `agents agent ask codex - < tmp/prompt.md`.
4. Read the result. Stdout is one JSON line: `run` (the run folder), and `results`,
   one entry per target with `status` (`ok`, `failed`, `timeout`, `too-large`,
   `empty`, `unsupported`, `missing`), `answer` (a Markdown file), `exit_code`,
   `seconds` and `stderr_tail` on failure. Read each `answer` file.
5. Present, then stop (see below).

The command adds a preamble for you: the asked agent is told it is consulted
read-only, and is given the absolute paths of the project's instruction files it does
not load itself (for Codex, `CLAUDE.md` and `.claude/rules`; for Claude, `AGENTS.md`
and `.kiro/steering`) plus your own global instruction files. Do not repeat that.

## Prompt templates

Investigation or debugging:

```
# Investigation request
## Problem
[2-3 sentences]
## Context
- Files: [path:line, ...]
- Observed: [...]  Expected: [...]
## What we tried
[approaches and outcomes, or "first consultation"]
## Question
[one specific question]
Give the root cause, a concrete recommendation with file:line references, and why the
earlier approaches failed. Keep it focused.
```

Adversarial code review (structured answer):

```
You are performing an adversarial code review. Find the strongest reasons this change
should not ship yet. Scope: [paths, branch diff or description]. Focus: [area or
"general"].
Prioritize expensive or hard-to-detect failures: trust boundaries and permissions;
data loss, corruption and irreversible state; rollback, retries, partial failure and
idempotency; races, ordering and stale state; empty, nil, timeout and degraded
dependencies; version skew and migrations; observability gaps.
Report only material findings defensible from code you can see. For each: what can go
wrong, why this path is vulnerable, the likely impact, and the concrete change that
reduces the risk. State inferences as inferences. No style nitpicks.
Return ONLY JSON: {"verdict": "approve"|"needs-attention", "summary": "...",
"findings": [{"severity": "critical"|"high"|"medium"|"low", "title": "...",
"body": "...", "file": "...", "line_start": N, "line_end": N, "confidence": 0.0-1.0,
"recommendation": "..."}], "next_steps": ["..."]}
```

## Presenting the answer

- Give the asked agent's analysis without session noise, then your own assessment:
  where you agree, where you disagree, and why.
- For a JSON review: list findings by severity (critical to low), skip confidence
  below 0.3, and mark which you think are false positives. With no findings, say the
  agent found no material issues.
- With several agents, show where they agree and where they differ.
- Then stop and ask the user what to do. An asked agent's findings are input for
  discussion, not work orders: do not apply fixes without the user's approval.

## Rules

- Read-only always: the adapters run each agent in its read-only or plan mode. Never
  ask an agent to edit files; there is no write mode.
- One question per run. Separate concerns get separate runs.
- An asked agent cannot ask again (`AGENT_UTILS_ASK_DEPTH`); if you were asked
  yourself, answer from your own analysis.
- `--file` and `--diff` refuse secret-looking files (`.env`, keys, credentials) and
  private keys. Never paste secrets into the prompt.
- The asked agent can be wrong. Check its claims against the code before relying on
  them.

## Troubleshooting

- `missing`: the CLI is not on PATH. `unsupported`: its version lacks a flag; update
  the CLI or report it, do not work around the read-only flags.
- `failed` with an auth message in `stderr_tail`: the user must log in to that CLI.
- `timeout`: raise `--timeout` (seconds, default 900) or narrow the question.
- An answer that ignores the read-only instruction or asks for permission: the agent
  hit a denied tool; rephrase so it only reads, or embed what it needs.
