---
id: inbox-agterm-companion
title: Open Inbox threads in agterm
summary: Inside agterm, show each Inbox thread you wait on as a companion page of your own session
applies-to: all
default: on
conflicts:
---
# Rule: open Inbox threads in agterm

Inside agterm (`AGTERM_ENABLED=1`), after every Inbox message that waits for the owner
(a question, decision, report or document), also show its thread as a companion page of
your own session. Outside agterm, or if `agtermctl` is missing, skip this silently; the
chat link stays mandatory.

```
agtermctl session overlay open --url "<local Inbox URL>#thread=<thread id>" \
  --companion --js --navigation --target "$AGTERM_SESSION_ID" --socket "$AGTERM_SOCKET"
```

- Local Inbox URL: the `URL:` line of `plannotator inbox status` (http://localhost:<port>/).
  The overlay runs on this Mac, so do not use a remote or proxy link here.
- Do not pass `--follow`: open it quietly, the owner switches when ready.
- One companion page per thread. If you already opened this thread in this session,
  do not open another; the open page updates itself.
- Never use a split pane or a session-wide overlay for this. Companion pages only.
- Close nothing yourself; the owner dismisses the page.
