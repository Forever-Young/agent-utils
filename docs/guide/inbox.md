# The Plannotator Inbox (optional)

[Plannotator](https://github.com/backnotprop/plannotator) is a separate tool. Its Inbox
is one local browser window where agents leave you messages, questions, files and
reviews, and where you answer later without holding a session open. agent-utils does
not need it. If you use it, agent-utils adds:

- an instruction snippet that tells every agent what to send there;
- a health check, and commands that give threads without an agent a session;
- hooks and an optional relay that tell [Kiro](https://kiro.dev) sessions about your replies;
- edits the Inbox has no button for (rename, reopen, move a thread).

agent-utils reads the Inbox's own files. Their layout is undocumented and was checked on
Plannotator 0.29.0. Edits still run on other versions: each one is backed up, checked through the
running Inbox afterwards, and undone from the backup (with a ⚠️ message) if that check fails.

## Tell agents to use it

The bundled `inbox-policy` snippet is on by default in the [shared
instructions](instructions.md). It asks agents to send questions (as `:::question`
blocks), final reports of non-trivial work, long code or commands, delayed follow-ups,
documents to sign off and guided code reviews to the Inbox, and not to open a blocking
Plannotator review unless you ask for one. Agents reach the Inbox through their
Plannotator tool (Kiro: the `plannotator-inbox` MCP server; Claude Code:
`plannotator_inbox`) or `plannotator inbox call send_message` from a shell.

## Check that it runs

```sh
agents admin inbox doctor               # exit 1 when something is wrong
agents admin inbox doctor --fix --yes   # restart it (plannotator inbox restart)
```

`doctor` checks the status record, that its process lives, that only one Inbox server
runs, that the page answers within 3 s and an API call within 15 s.

## Threads without an agent

A reply goes to the last agent session that wrote in the thread. Messages sent with
`plannotator inbox call` from a plain shell carry no session, so nobody receives the
reply. An agent adopts such a thread by replying into it (`reply_to` its latest
message) with its own Inbox tool. `dispatch` does that for you:

```sh
agents admin inbox threads                       # threads without a session, with target window/workspace
agents admin --dry-run inbox dispatch --answered # preview
agents admin inbox dispatch --answered           # or --thread ID (repeatable), or --all
```

For each selected thread, `dispatch` starts a new session in the configured project
whose root matches the thread's project (an exact root wins over a parent folder), in
the thread's folder, titled with its subject. The first prompt tells the agent to read
the thread, reply into it and continue. `--agent` overrides the project's default
agent; `--window` and `--project` narrow the targets. Each thread is dispatched once
(recorded in `~/.local/state/agent-utils/inbox.json`); `--force` dispatches again.

Sessions that Plannotator itself started from the Inbox run in its background process:

```sh
agents admin inbox launches                  # with the matching agterm workspace
agents admin inbox adopt --launch LAUNCH_ID  # open an agterm row attached to it
```

Claude (`claude attach`) and Codex (`codex resume`) only.

### Adopt them automatically

A session you start from the Inbox on your phone has no agterm row until it is adopted.
A small scheduled job does that for you, without a loop that keeps running: every few
minutes launchd starts `agents admin inbox adopt --auto`, which opens a row for each
new launch and exits (nothing is printed when there is none). Each launch is adopted once.

```sh
agents admin config run optional inbox-adopt --yes   # install the job (also in `agents configure`, Hooks)
agents admin config set inbox.adopt_interval_minutes 10 --yes   # default 5; install again to apply
agents admin config set inbox.auto_adopt off --yes   # pause without removing the job
agents admin config run remove-inbox-adopt --yes     # remove the job
```

`agents admin config run optional --detected --yes` also installs it when `agtermctl` and
`plannotator` are found. Without agterm the job does nothing.

## Edit a thread

```sh
agents admin inbox rename --thread ID --name "Parser fix"     # --clear shows the subject again
agents admin inbox subject --thread ID --subject "New subject"
agents admin inbox reopen --thread ID
agents admin inbox author --thread ID --message MSG_ID --name Kiro
agents admin inbox withdraw --thread ID --question MSG_ID/KEY
agents admin inbox move --thread ID --destination ~/Projects/hobby
```

Each previews first. With `--yes` it stops the Inbox, edits its files with a backup and
starts it again. The `plannotator-state` skill (`skills/plannotator-state`) lets agents
make these edits when you ask.

## Kiro and Inbox replies

Plannotator does not wake Kiro when you reply. The [Kiro hooks](notifications.md#kiro-attention-hooks-opt-in)
handle the rest:

- Kiro session start and end are recorded in `inbox-relay.json` in the state dir.
- After the session sends a message into a thread (MCP `send_message` or
  `plannotator inbox call send_message`), that thread is connected to the session.
  Reading does not connect. Connections survive the session's end and are dropped
  after 60 days.
- On each prompt and at `Stop`, for your unread replies in connected threads, the hook
  prints `Plannotator Inbox: the person replied in thread <id> (message <id>). Read it
  with read_thread.` (at most 3 times per reply; nothing when the Inbox is stopped; it
  never starts the Inbox).

### Wake Kiro on a reply

```sh
agents admin inbox relay                   # run in a spare terminal; Ctrl-C stops it
agents admin inbox relay --once --dry-run  # one pass, log only
python3 scripts/configure-relay.py --apply # or keep it running as a launchd job
python3 scripts/configure-relay.py --remove --apply
```

The relay types the same pointer line and Enter into the agterm pane of a live Kiro session connected to the thread (the most recently connected one), once per reply, and only when the session has been idle for `--idle-seconds` (default 10) and the pane's last line is Kiro's empty prompt.
A half-typed prompt is never touched.
`--interval` sets the seconds between passes (default 8).
The launchd job starts at login, restarts on exit and logs to `~/.local/state/agent-utils/inbox-relay.log`.

## Use the Inbox from a phone over Tailscale

The Inbox listens on `127.0.0.1` on a port that changes when it restarts.
`plannotator inbox status` prints the current addresses:

```text
Plannotator Inbox: running
  URL:      http://localhost:PORT/
  tailnet:  https://my-mac.example-tailnet.ts.net:PORT/
```

The `tailnet:` line appears when the Inbox is published on your tailnet; that address
is reachable only from your own Tailscale devices. Because the port changes on
restart, a bookmark on the phone breaks. For a stable address, put a reverse proxy on
the Mac with a fixed tailnet name (for example Caddy serving
`https://inbox.example-tailnet.ts.net/`) and point its upstream at the current port;
update the upstream after each Inbox restart, for example from a launchd job that reads
`plannotator inbox status`. agent-utils does not configure Tailscale or a proxy; a copyable example is in
[docs/addons/inbox-proxy](../addons/inbox-proxy/README.md).

To make agents link to that address, add a private snippet in
`~/.config/agent-utils/snippets/inbox-links.md` that names your stable URL and the
fallback (`https://my-mac.example-tailnet.ts.net:PORT/`, read from `plannotator inbox
status`), then `agents admin instructions compose --add inbox-links --yes`. A thread
link is the address plus `#thread=<thread id>`. Keep real host names out of
repositories.
