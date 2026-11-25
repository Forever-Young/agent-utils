---
name: agterm-workflows
description: Operate agterm-utils projects, task worktrees, managed sessions, results and native remote attachment. Use for agents agterm or agents workflows; leave code review and model orchestration policies to the relevant provider skills.
---

# agent-utils workflows — draft for review

This folder is a review artifact, not an installed skill. Do not install or activate
it without the user's request. Its current command contract is described below.

Use `agents --help` and `agents GROUP --help` for the installed command surface.
Read [references/workflows.md](references/workflows.md) for the requested workflow.
Keep configuration, registry data, actual project inventories and provider histories
outside the repository.

## Choose the right operation

- Configure and send a project: `project add/edit ... --send` creates/reuses only its
  window/workspace. It does not start a model or move existing terminal rows.
- Development task: `task --project ID --task TASK-ID` creates per-repository
  worktrees and launches the configured provider there. Use an explicit `--in-place`
  only when the user intends the original roots. Never silently use the original
  checkout after a worktree failure. Partial worktrees remain for inspection.
- Ordinary conversation: `new` or native `resume`; use the configured provider.
  Coordinator is an explicit Claude-only action, normally one project's Main row.
- Existing managed session: focus first; exact resume requires a real conversation
  ID and evidence that its old provider process has ended. Utility IDs, terminal
  IDs and provider IDs are different. Never substitute one for another.
- Task result: call `session finish` with an actual outcome, tests and remaining
  blockers. Native turn completion/idle does not establish task completion.
- Peer review: invoke the existing review skill in the task's worktree. This skill
  does not prescribe reviewers, retry loops or permission rules.
- Remote: native Mac-to-Mac attachment through `agents remote`; ordinary SSH remains an
  ordinary shell. In an origin SSH shell, `agents terminal` attaches one Live pane
  with fresh inventory and an immediate existence check. Its remaining check/attach
  race can recreate a disappeared daemon; do not describe it as atomic.

Top-level config `new_session_position: top` moves utility-created rows and hooked
new shells to the top once. Run bootstrap after enabling it to baseline existing
rows without moving them. `default` disables it. Do not clear placement state to
make restoration reshuffle existing sessions.

## Preserve identity and scope

`AGTERM_UTILS_RECORD` identifies the current managed record in a provider's tool
shell. Use `session list --all` to inspect records; do not guess IDs from names or
timestamps. Bind a Codex ID only when it is the actual provider conversation ID;
project history discovery/adoption can find older conversations by exact cwd.

Utility hide/archive/forget only affects utility metadata. Provider mutation uses
explicit `provider-*` operations and requires authorization for that operation.
A request to tidy menus does not authorize provider transcript deletion, killing
processes, publishing or global terminal/provider configuration changes.

Read native agterm documentation before using lower-level session/daemon control.
A failed provider initialization can leave a registered row: inspect the terminal,
report the failure and preserve its conversation UUID. Never reset history to make
recovery appear successful. For ordinary resume, a missing row is not proof that
its Live process stopped.
