---
id: inbox-links
title: Inbox links
summary: Always give the Inbox thread link in the chat, preferring a stable remote address
applies-to: all
default: off
conflicts:
---
# Shared rule: Inbox links

Always give the thread link in the chat reply, not only a localhost one. When a local
instruction names a stable address for the Inbox (a tailnet or proxy URL), use it; otherwise
run `plannotator inbox status` and use the `URL:` it prints.

- Link to a thread by appending `#thread=<thread id>` to the address.
- When the stable address does not answer, fall back to the `URL:` from the status.
- Give the link every time you mention the Inbox, including when you only tell the owner
  where it is.
