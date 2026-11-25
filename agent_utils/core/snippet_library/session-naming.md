---
id: session-naming
title: agterm session names
summary: Topic-first sidebar names, renamed once per topic or phase
applies-to: all
default: on
conflicts:
---
# Shared rule: session names

Inside agterm (the `AGTERM_SESSION_ID` variable is set), keep the sidebar name of your
session useful. Outside agterm, skip this silently. Do not wait for a hook reminder:
reminders arrive only with typed prompts, not with turns started by Inbox replies or
other plugin messages. Tie every rename to an event you cause yourself.

Rename (`agents agent rename "<name>"`) at each of these moments. Skipping one is a
failure even when the task went well:

1. Your first tool call, when the topic is not yet known: use a neutral placeholder,
   `<repo name>, start`. Never describe or paraphrase the request.
2. Right after your first read of the task (files, code, ticket), once the topic is
   known, and before your first edit or any command that changes something: rename to
   `<topic>, <phase>`, replacing the placeholder or any earlier name. The tool call
   after that first read is this rename. A name that only restates the request is wrong.
3. Before every `git commit`: rename if the phase changed since the last rename.
4. Before every message you send to the Inbox, and before your final reply, run this
   check: run `agents agent rename` unless the current name already ends in the current
   phase. When the work is finished the phase is `done`: `<topic>, done`.

Phases: `start`, `assess`, `plan`, `implementation`, `review`, `done`. A new phase begins
when you move from reading to editing, from editing to checking, or finish. A task that
reaches 10 or more tool calls and changes phase must show the new phase in the name.
A turn started by an Inbox reply or any message that is not a typed prompt is a new
moment 2: if the name is the placeholder or stale, rename before working.

- Name the topic of the work (plan, ticket, feature or problem), not the first message.
  Example: `Login rework, implementation`.
- Format: topic first, action last, comma separated: `Self-destructive sessions, assess`,
  later `Self-destructive sessions, implementation`.
- No agent or tool prefix (`kiro`, `claude`, `codex`) and no emoji. Keep about 30
  characters before the action, because the sidebar is narrow.
- Do not rename when the topic and phase are unchanged and the name already says them.
- If the project's own rules define a naming format (for example a ticket key first),
  follow it instead of the topic-first form.
- `agents agent rename` renames the agterm session and, where the agent allows it, your
  own session in the agent's session list, and prints what it did. In Kiro, also call
  your `update_session_information` tool with the same title. Kiro only prints that
  instruction, it does not set the title itself. If the tool is deferred, load it first,
  then call it; a failed call must be retried, not skipped. If `agents` is not installed,
  use `agtermctl session rename "<name>" --target "$AGTERM_SESSION_ID"`.
