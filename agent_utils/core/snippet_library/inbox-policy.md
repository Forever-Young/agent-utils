---
id: inbox-policy
title: Plannotator Inbox policy
summary: What goes to the Plannotator Inbox instead of the chat; no blocking reviews
applies-to: all
default: on
conflicts:
---
# Shared rule: the Plannotator Inbox

Send these to the Plannotator Inbox, not only to the chat:

- A question or decision for the owner, each as a `:::question` block.
- A final report of non-trivial work: what was done, what changed, what remains.
- Code blocks longer than about 8 lines, or commands the owner should copy. Put them
  in the message or an attached file; the browser is easier to copy from.
- Delayed or long-running follow-ups ("after the deploy, wait an hour, then run
  this"). Send the steps as `:::action` items or schedule them with
  `plannotator routine`, and say when you will check back.
- A plan, design, spec or other document that needs the owner's read or sign-off.
  Attach the file in `attachments` and put each decision in a `:::question` block.
- A finished code change worth reviewing (a new module, several files, a risky
  change): send a guided review through the Inbox (`get_guide_brief`, then
  `submit_guide`).

Do not open a blocking Plannotator review (plan review, `annotate`, `review`) for
plans, documents or code changes. This overrides Plannotator's own guide, which
suggests a blocking review for sign-off. Open one only when the owner asks for a
"review" or "annotate" session, or when the Inbox cannot be reached.

After sending, do not wait or poll. Continue any work that does not depend on the
owner's answer; when the next step does depend on it, end the turn. Either way, say
in the chat what is waiting, with the thread link. An agent that gets no wake-up
when the owner replies (Kiro) reads the thread on its next turn or when told.
In Kiro, list unread threads (`read_thread` with no id) at the start of a turn, and
before ending a turn that sent a question, so a reply that arrived meanwhile is not
missed. A hook may print a pointer line naming a thread; read the reply itself with
`read_thread`.

Worth sending means: it has a decision, needs more than a screen of reading,
includes pictures or files, or will be read later. A fact, a one-line result, a
small fix or a status that needs no reply stays in the chat. Use one thread per work
stream.

Always give the thread link in the chat reply. When a local or per-agent
instruction names a host for Inbox links (for example a tailnet address), use it.

The Inbox names a project after its folder; the name cannot be set. To group several
folders under one agent-utils project, move their threads into one Inbox project with
`agents admin inbox move` (the `plannotator-state` skill), after the owner agrees.

When no Inbox tool is loaded, use `plannotator inbox call send_message`.
