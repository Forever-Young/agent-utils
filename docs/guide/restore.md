# Reopen agterm into the same state

agterm restores windows, workspaces, row names, folders and layout by itself, and
replays each pane's command. For an agent pane that command is a bare launch, which
opens a new conversation or the session chooser. `agents agterm restore` fixes that by
giving each agent pane a restart command (agterm calls it a pin): the agent's own
resume command for that pane's exact conversation.

## Set it up

```sh
agents agterm restore sync       # bind conversation ids, write restart commands
```

`sync` has to run after a new conversation exists, so run it from agterm's status hook.
Add this line to `~/.config/agterm/hooks.conf` (keep a backup), with the full path of
your `agents`:

```text
on status /path/to/agents agterm restore sync
```

Then `agtermctl hooks reload`. Undo: delete the line and reload.

## What `sync` does

- Conversation ids come from the session records. Claude ids are known at launch. For a
  running Codex or [Kiro](https://kiro.dev) pane without an id, `sync` reads it from the provider's open
  rollout or lease file (read-only) and binds it only when exactly one candidate
  matches.
- Codex and Kiro panes you started by hand are registered too (project `manual`): it
  finds the provider process by the agterm session id in its environment.
- A new managed launch and `session bind` write the restart command at once.
- When an agent ended and the pane is now something else, such as a plain shell, `sync`
  clears our restart command after it sees that twice at least 60 seconds apart, so the
  pane comes back as a shell. A pane that is replaying its command is left alone.
  Hidden or archived records lose their restart command on the next `sync`.
- Restart commands you set yourself are never touched. Nothing is typed into panes and
  no conversation is resumed by `sync` itself.

## Panes `sync` cannot find

```sh
agents agterm restore pin                                   # choose a row and agent
agents agterm restore pin --target SESSION_ID --agent kiro-cli --conversation ID
```

`pin` lists recent Kiro conversations for the pane's folder, validates the id and
registers the pane. Use it for other providers, ambiguous processes and agents started
by hand.

## Snapshots

```sh
agents agterm restore snapshot   # versioned snapshot under the state dir, 3 kept
agents agterm restore plan       # compare the last snapshot with the live tree, read-only
```

## Live mode and rerun mode

agterm applies restart commands only in rerun mode; `agtermctl session restore` says so
in Live mode. In Live mode a recreated pane replays its foreground command. For managed
launches that is the `runner.py` wrapper, which on a replayed start swaps in the resume
command of the recorded conversation. Panes started by hand have no wrapper and replay
bare in Live mode; they need rerun mode.

Restart commands are shell code stored in agterm's state. They contain the record id,
resume id, folder and configured launch arguments, so keep secrets out of launch
arguments.
