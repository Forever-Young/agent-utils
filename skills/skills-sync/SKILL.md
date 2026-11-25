---
name: skills-sync
description: Add a skill once and make it available to every installed coding agent (Claude, Codex, Kiro, Cursor, OpenCode, Pi, Kimi, Qwen, Copilot, Vibe, Antigravity), adapting Claude-only instructions so other agents can follow them. Use when the user says "add this skill to all agents", "sync skills", "share this skill", "install skill X everywhere", "make this skill work in Kiro/Codex", or asks why an agent lacks a skill. Also offers marketplace (plugin) skills to agents that cannot install that marketplace.
---

# Skills sync

`agents admin skills` (from agent-utils) does the file work; this skill decides and adapts.
Run `agents admin skills --help` and `agents admin skills <command> --help` first if a flag is unclear.
Every mutating command previews; repeat it with `--apply` only after reading the preview.

## Model

- `~/.agents/skills` is the shared store: a skill there is for every agent. Codex,
  OpenCode, Cursor, Pi, Kimi, Qwen, Copilot and Vibe read it directly; Claude, Kiro and
  Antigravity get symlinks. Never copy a skill into an agent folder by hand.
- A skill for only some agents stays in those agents' own folders (`agents admin skills
  exclude NAME --agent claude|kiro|agy` hides a store skill from a linked agent only).
- `managed` skills belong to an installer (AWS toolkit, twg, Plannotator, Codex system).
  Do not edit, move or adapt them; update them with their installer.
- A store skill with a skills CLI lock entry (`npx skills`) is overwritten by
  `npx skills update`. Do not adapt it in place; tell the user (see "Upstream skills").
- Marketplace skills (Claude, Codex, Cursor plugins; Gemini extensions) are imported
  into `~/.local/share/agent-utils/skills/NAME` and linked only into agents that lack
  that marketplace. The import records its source; `check` reports when it changed.

## Add a skill to all agents

1. Find the source: a folder, a git URL (`URL#path/to/skill`), or a skill that already
   sits in one agent's folder.
2. Folder or URL: `agents admin skills add SRC` then `--apply`. Already in an agent folder:
   `agents admin skills sync` then `--apply` (it moves the newest copy into the store, backs
   up other copies, and links Claude, Kiro and Antigravity).
3. Run `agents admin skills check`. If the new skill is listed under "Claude-only tools",
   adapt it (below), then `agents admin skills reviewed NAME --apply`.
4. Report what was added, what was adapted (show the diff), and that agent sessions
   must be restarted to see the skill.

## Sync everything

1. `agents admin skills sync`, read the preview: `move to store`, `backup` (identical or older
   copies, duplicates), `link`. Ask before applying when a `backup` drops content that
   differs from the winner ("older content") and the user did not ask for a full sync.
2. `agents admin skills sync --apply`, then `agents admin skills status`. Remaining non-ok rows need
   a decision; explain them.
3. `agents admin skills check` and handle its three lists (adapt, stale imports, offers).

## Adapt Claude-only instructions

Edit the store or import copy so every agent can follow it, keeping Claude behavior:

- Structured question tool (AskUserQuestion): "Ask the user (use a structured question
  tool if the agent has one, otherwise ask in chat and wait)".
- Task / subagent tools (Task, TaskCreate, subagent_type): "Delegate to a subagent if
  the agent supports it; otherwise do the step yourself, in order".
- `${CLAUDE_PLUGIN_ROOT}`, `${CLAUDE_PLUGIN_DATA}` and similar: make paths relative to
  the skill folder. Copy any script the skill needs from the plugin into the skill
  folder (`scripts/`), and replace the variable with "this skill's folder".
- `mcp__server__tool` names: describe the capability and name the server, so an agent
  with the same server under another name finds it.
- TodoWrite, ExitPlanMode, EnterWorktree: describe the intent (keep a task list, present
  the plan and wait, work in a git worktree).
- Keep `allowed-tools` in the frontmatter; other agents ignore it.

Do not change what the skill does. Keep edits minimal and show the diff. Then run
`agents admin skills reviewed NAME --apply` so `check` stops listing it.

## Marketplace skills

`agents admin skills check` lists "Marketplace skills other agents lack" with the agents that
have them natively. Ask the user which to import (send the list where they read
decisions, e.g. the Plannotator Inbox, one multi-choice question). For each chosen:
`agents admin skills import SOURCE --apply`, adapt if listed, `reviewed`. A stale import:
re-run `import` with the new source path shown by `check`, adapt again, `reviewed`.
A marketplace skill meant for one project only: `agents admin skills --project DIR add SOURCE
--apply` (no source tracking in project scope), then `agents admin skills dismiss NAME --apply`
so `check` stops offering it. Codex `openai-*` and `claude-plugins-official` skills depend on their own agent and are
hidden unless `check --all-markets`.

## Upstream skills

`check` marks skills installed by the skills CLI with their upstream. Their store copy
is shared with every agent, but an update overwrites edits. Options to offer the user:
leave as is; ask upstream for agent-neutral wording; or remove the lock entry
(`npx skills remove NAME`, keeping a copy) and `agents admin skills add` the adapted copy,
which then updates only through this skill.

## Safety

- Backups go to `~/.local/state/agent-utils/backups/<timestamp>/`. To undo, move the
  folder back and run `agents admin skills sync --apply`.
- Never delete a skill folder directly; use `agents admin skills remove NAME --purge`.
- Never edit files under plugin caches, `~/.config/twg`, or other installer folders.
