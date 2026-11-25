# Session names

`agents agent rename "<name>"` gives one name to the agterm row and, where the agent
allows it, to the agent's own session (the name in its resume picker). The bundled
`session-naming` instruction snippet tells agents to run it at moments they cause
themselves, so it does not depend on a hook reminder (reminders come only with typed
prompts, not with Inbox replies): a placeholder `<repo name>, start` on the first tool
call, the real topic right after the first read, before every `git commit`, and before
every Inbox message or final reply unless the name already ends in the current phase
(`..., done` at the end). Form: `Topic, action` (`Parser fix, review`).

```sh
agents agent rename "Parser fix, implementation"
agents agent sync --name "Parser fix, implementation"   # copy a name to agterm by hand
```

`rename` prints what it did for each side. Outside agterm it does nothing to agterm.

## What each agent supports

| Agent | Agent's own session name | Agent's name copied to agterm |
| --- | --- | --- |
| Codex | set at once (app-server `thread/name/set`) | on start/resume and each prompt (hooks) |
| Claude, Qwen | set at the next prompt (`UserPromptSubmit` hook `sessionTitle`) | on start/resume (`session_title`) and each prompt |
| Pi | set after the tool call (extension) | on start/resume and on `/name` (extension) |
| OpenCode | set at once (`opencode api session.update`) | no |
| [Kiro](https://kiro.dev) | only through Kiro's own `update_session_information` tool | on start and each prompt (hooks read `session.json`) |
| Copilot, Cursor, Kimi, Vibe, Antigravity | not set; agterm only | no |

For Kiro, `rename` prints an instruction for the model to call its
`update_session_information` tool with the same title; it cannot set the title itself.

## Install the hooks

```sh
agents admin config run optional session-names        # preview
agents admin config run optional session-names --yes  # hooks and the Pi extension link
agents admin config run remove-session-names --yes    # undo
```

Install agent-utils first: the hooks run the installed `agent_utils/agterm/names.py`.
Only agents whose executable is found are configured (`--all` configures the rest too),
only entries that run `names.py` are added or removed, and every changed file is backed
up beside itself.

| Agent | File |
| --- | --- |
| Claude | `~/.claude/settings.json`, `SessionStart` and `UserPromptSubmit` hooks |
| Qwen | `~/.qwen/settings.json`, the same hooks |
| Codex | `~/.codex/hooks.json`, `SessionStart` and `UserPromptSubmit` |
| Kiro | `~/.kiro/hooks/agent-utils-session-names.json`, `SessionStart` and `UserPromptSubmit` |
| Pi | `~/.pi/agent/extensions/agent-utils-session-name.ts`, a symlink to the installed copy |

After installing:

- **Codex** skips new or changed hooks until you trust them once with `/hooks` inside
  Codex. Do it again after every hook change.
- Restart running agent sessions so they load the hooks.

## How copying works

An agent's name is copied to agterm only when it changed since the last copy for that
row, so a name set with `rename` is not undone by an older one. A name typed in the
agterm sidebar is not copied to the agent. Claude transcripts are never edited.

On each prompt inside agterm, the hook reminds the model to run `agents agent rename`
(Kiro and Codex: printed text; Claude and Qwen: `additionalContext`) while the session
was never named through the command, and again every 8th prompt after a rename to
re-check topic and phase. The marker is in
`~/.local/state/agent-utils/session-names/named/`. Agents without a prompt hook (Pi,
OpenCode, others) get only the instruction text. A model can ignore the reminder;
nothing blocks its work.



Reference: [agent.md](../reference/agent.md#agents-agent-rename),
[setup.md](../reference/setup.md).
