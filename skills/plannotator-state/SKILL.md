---
name: plannotator-state
description: Change Plannotator Inbox things that have no tool or UI control. Use when the person asks to rename a Plannotator Inbox thread or change its title or name, change a thread's subject, reopen a resolved thread, move a thread to another project, withdraw an open question, fix an agent author name, or when a thread shows a snake-case name instead of its subject.
---

# Plannotator Inbox state edits

Use this only for a change the person asked for that `plannotator inbox --help`, the
`plannotator-inbox` tool and the Inbox window cannot make. A supported path always wins
(`send_message` with `supersedes` withdraws your own question; `send_message` with `thread`
names an unnamed thread once; `resolve_message` archives).

All commands come from `agents admin`. Each is a preview until `--yes`:

| Change | Command |
| --- | --- |
| Thread title (the name shown in the list) | `agents admin inbox rename --thread ID --name "New title"` or `--clear` (show the subject) |
| Subject of a thread | `agents admin inbox subject --thread ID --subject "New subject"` |
| Reopen a resolved thread | `agents admin inbox reopen --thread ID` |
| Author name/host of one agent message | `agents admin inbox author --thread ID --message MSG --name NAME [--author-host HOST]` |
| Withdraw an open question | `agents admin inbox withdraw --thread ID --question MSG/KEY` |
| Move a thread to another Inbox project | `agents admin inbox move --thread ID --destination PROJECT` (id, key, name or root of an existing project) |
| Merge two Inbox projects | `agents admin inbox move` for each thread of the one into the other; the emptied project stays, and new messages from its folder reopen it. The Inbox names a project after its folder and has no rename; a name cannot be set. |

- Get `ID` from `read_thread` (the first message of the thread), `MSG/KEY` from its
  question list.
- Run the preview first and show it to the person. Get a yes before `--yes`: the Inbox is
  stopped for a few seconds and every connected agent reconnects.
- With `--yes` the command stops the Inbox, backs up every touched file to
  `<data-dir>/inbox/agent-utils-backups/`, rewrites the records atomically, reads the files
  back, restarts with `plannotator inbox --background`, runs the Inbox health check and asks
  the running Inbox (`read_thread`) whether it shows the change. If any step fails it writes
  the originals back, restarts the Inbox again and exits 1 with a message that starts with
  ⚠️ and names the backups.
- A Plannotator version the edits were not checked on (only 0.29.0 so far) does not stop
  the edit; the report then has a `version_note`. When the person asked for the change, run
  it: the backup, the check through the Inbox and the automatic restore are the safety net.
- Limits: titles 1-80 characters and unique among open threads; subjects up to 200;
  a title clash blocks reopen and move; an answered or sent question cannot be withdrawn;
  a moved thread keeps absolute attachment paths.
- The restart is expected and fine. Checked: `rename --yes` ended healthy, the thread
  title changed, and the port stayed the same (it can change). If the person has a reverse proxy in front of the Inbox, it may answer 502 for
  about a minute after the restart until its sync job picks up the new port. Read the direct URL with `plannotator inbox status`. Give the
  person the Inbox link their instructions name; if it still fails after a minute, use the tailnet URL.
- Afterwards confirm with `read_thread`. Keep the backups until the person confirms.

## Rules

- Never start the Inbox with plain `plannotator inbox` or `--no-open`: that counts as the
  person's start and writes `plannotator-inbox` MCP entries into other agents' configs.
- Never edit `inbox.json`, leases or blobs by hand, other projects' files, or any file
  while the Inbox runs. For a change not listed here, tell the person; do not hand-edit.
- If the command exits 1 with a ⚠️ message, tell the person in the chat and in the thread,
  starting the line with ⚠️: what failed, that the originals were restored, the backup
  paths, and whether the Inbox came back healthy. If it is still unhealthy, run
  `agents admin inbox doctor --fix --yes` and report its result. Do not retry the edit
  without a new request.
