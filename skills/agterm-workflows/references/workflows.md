# Practical command recipes

All names/paths are synthetic. Substitute the configured project ID and actual
repository roots. Configuration defaults to `~/.config/agent-utils/config.json`;
managed state defaults to `~/.local/state/agent-utils/registry.json`. CLI global
options (`--config`, `--dry-run`) precede the command.

## Add, edit and show a project

```sh
agents admin project add --project example --name Example \
  --window Work --agent claude --root ~/Projects/example --send
agents admin project edit --project example --root ~/Projects/example \
  --root ~/Projects/shared
agents admin project order --project example --position 1
agents admin project move --project example --window Personal
agents admin project disable --project example
agents admin project enable --project example --send
agents agterm list
```

Add a new window with an explicit `--agent`. Repeating roots replaces the ordered
root list on edit. `--send` reuses existing window/workspace names and starts no
agents. Rename/move configuration doesn't migrate existing rows. Keep old rows
until the user chooses what to move or close.

## Place new sessions first

Set top-level `"new_session_position": "top"` in the external config, then run
`agents agterm bootstrap` to record existing rows without moving them. Utility
launches move their returned row ID; hooked +/Cmd-N shells move themselves once.
External `placement.json` remembers handled IDs so restoration doesn't move them
again. Without the baseline, an existing row first seen by startup can move once.
Rows from closed/unbaselined windows can move once on their first hook run.
Omit the key or set `"default"` to keep normal placement. No agterm-wide preference
is changed. External explicit launches that bypass the hook are not covered.

## Work coordinator plus independent Personal work

```sh
agents agterm coordinator --project example
agents agterm new --project hobby
agents agterm task --project example --task issue-123 \
  --description 'Improve parser' --prompt 'Implement issue-123 using project rules'
agents agterm task --project hobby --task personal-42 --description 'Add export'
agents agterm dashboard
```

The coordinator stays Claude-only even when a project's default is Codex. Regular
startup preselects a new conversation using the project/window provider. Task roots
must be Git repository roots. `--repo` selects repositories, `--destination` chooses
the worktree parent and `--base` selects the revision. Existing branches/targets and
dirty sources are refused. Do not clean or delete them merely to satisfy the command.
Normal conversations do not create task worktrees. Concurrent work uses different
task IDs/branches; peer review runs from the task worktree via the installed skill.

## Identity, history, recovery and results

```sh
agents agterm session list --project example
agents agterm history --project example --agent claude
agents agterm history --project hobby --agent codex
agents agterm history --project hobby --agent codex --adopt ACTUAL_UUID
agents agterm session bind --record RECORD_ID --conversation ACTUAL_UUID
agents agterm session focus --record RECORD_ID
agents agterm session resume --record RECORD_ID --backend-stopped
agents agterm session status --record RECORD_ID --state running
agents agterm session finish --record RECORD_ID --result 'Implemented; tests passed'
agents agterm session finish --record RECORD_ID --failed --result 'Blocked by missing API'
```

Use `--backend-stopped` only after confirming it; otherwise reattach the Live process.
History adoption creates metadata and does not start a model. New Claude UUIDs are
known before launch; new Codex and provider-picker UUIDs need actual binding. Do not
infer them from matching timestamps/cwd. Provider histories stay in provider storage.

Explicit results notify the worker and an open registered project coordinator.
Include the outcome and remaining blocker honestly. Agent idle/turn-completed status
must not be translated into successful task completion.

## Tidy utility views without changing transcripts

```sh
agents agterm session hide --record RECORD_ID
agents agterm session unhide --record RECORD_ID
agents agterm session archive --record RECORD_ID
agents agterm session unarchive --record RECORD_ID
agents agterm session list --all
agents agterm session forget --record RECORD_ID --yes
```

Those commands don't close terminals or delete provider transcripts. Codex provider
archive/unarchive/delete is a separate `session provider-*` action against a bound
UUID. Archive/delete needs `--yes --backend-stopped`; an open recorded row is refused.
Claude per-conversation provider deletion is unsupported. Never replace it with a
project-wide purge. Get user authorization before irreversible provider deletion.

## Remote work from another Mac

```sh
agents remote shell --host dev-mac
agents remote list --host dev-mac --agents-only
agents remote attach --host dev-mac --window Work --project Example --companions
agents remote attach --host dev-mac --session EXACT_ORIGIN_ID
agents remote detach --session LOCAL_ATTACHED_ROW_ID
agents remote lead --session LOCAL_ATTACHED_ROW_ID
```

Origin and destination IDs differ. `--destination-window` is a local window ID;
`--window`/`--project` filter origin names. Key-based SSH, running agterm on both Macs
and Live origin panes are required. Detach closes only a verified attached client
row. Leadership/resize/reconnect are agterm's. Do not use `zmx detach` (all clients),
kill a daemon to disconnect a client.

For phone/Termius, SSH into the origin Mac and run `agents` or `agents remote terminal`:

```sh
ssh -t dev-mac 'agents remote terminal'
agents remote terminal --session EXACT_ORIGIN_ID --pane left
```

Terminal mode selects one pane per connection, revalidates inventory/endpoint and
runs bundled `zmx get` immediately before attach. Ctrl+\ detaches only this client;
Escape leaves the SSH shell available. Separate panes require separate connections.
No GUI overlay mirroring or native Mac leadership controls are added.
The accepted check/attach race remains: a daemon disappearing after the check can
be recreated by raw zmx. Never claim existing-only/atomic attachment or kill a
replacement automatically. Use the discovered bundled endpoint, not another zmx.

## Images, desktop and input helpers

```sh
agents agterm gui --project hobby
agents agterm gui --project example --agent claude --session ACTUAL_UUID
```

Codex opens the workspace; this does not establish GUI project/settings equivalence.
Image paste uses native provider clipboard handling; confirm a preview before send.
OS permissions remain normal. Claude prompt spelling uses a local checker and user
`spellcheck` settings; shell syntax highlighting applies only to the shell. Provider
review, grammar tools and editor linting belong to their own integrations.

## Install and undo only when requested

```sh
python3 scripts/install.py --with-tui
python3 scripts/install.py --with-tui --apply
python3 scripts/install.py --list-backups
python3 scripts/install.py --rollback
python3 scripts/install.py --rollback --apply
```

Config/history/worktrees are preserved. TUI runtime is separate from code rollback.
Startup hooks are opt-in through `scripts/configure-zsh.py`; do not change startup,
restore policy or model configuration merely to test a task. The draft skill must
not install itself or change its own invocation policy.
