# Notifications and attention

Two separate things tell you that an agent needs you:

- **agterm attention marks**: the row shows blocked, active or completed. agterm's own
  agent status hooks (**Help → Install Agent Status Hooks**) do this for the agents
  agterm supports; the opt-in [Kiro](https://kiro.dev) hooks below add it for Kiro.
- **Push notifications** through ntfy, to your phone or desktop.

## Push notifications (ntfy)

Off by default, one transport: an ntfy URL. Configuration is
`~/.config/agent-utils/notify.json`.

```sh
agents admin notify url https://ntfy.sh/YOUR-TOPIC
agents admin notify enable          # needs the URL first; disable turns it off
agents admin notify test            # sends one event; agents admin notify --dry-run test previews
agents admin notify show
agents admin notify template --title '{session}' --body '{event_text}{detail_suffix}'
```

Agents and hooks send events:

```sh
agents agent notify send --agent codex --event done --detail 'tests pass'
```

- `--event`: `done`, `input`, `failed` or `info`.
- `--session` defaults to `AGENT_UTILS_SESSION_NAME`, then the agterm row name, then
  the folder name.
- Template placeholders: `{agent} {session} {event} {event_text} {detail}
  {detail_suffix}`.
- A disabled or unreachable setup prints nothing or one line and exits 0, so a hook
  never fails because of it. `--quiet` prints nothing at all.

`agents agent notify` and `agents admin notify` are the same command; agents use the
first, people the second.

### Only when you are away

On macOS a push is skipped while you are at the computer: the last keyboard or mouse input,
in any app, was less than `away_after_seconds` ago (default 120). The agterm row marks and
local notifications still show. Change it in `agents configure` (Notifications), with
`agents admin notify away-after 300`, or `agents admin config set notify.away_after_seconds 300`;
0 always sends. A locked screen counts as away too and sends at once; turn that off with
`agents admin notify away-when-locked off`. `agents admin notify test` ignores the check. Where the idle time cannot be read the
push is sent as before.

### Use it from Claude Code and Codex

agent-utils does not change Claude Code or Codex configuration. To replace your own
notification scripts, point their hooks at:

- `agents agent notify hook claude` (reads the hook JSON on stdin; `Stop` becomes
  `done`, `Notification` becomes `input`);
- `agents agent notify hook codex '<json>'` (`agent-turn-complete` becomes `done`).



## Kiro attention hooks (opt-in)

Kiro CLI has no per-launch hook option, so the hooks live in one managed global file.
Install agent-utils first (it installs `agent_utils/agterm/kiro_hook.py`), then:

```sh
agents admin config run optional kiro-hooks        # preview
agents admin config run optional kiro-hooks --yes  # write ~/.kiro/hooks/agterm-utils-attention.json
agents admin config run remove-kiro-hooks --yes    # undo
```

| Kiro event | Effect |
| --- | --- |
| `PermissionRequest` (Kiro is about to ask for tool permission) | row marked blocked with blink; `input` notification |
| prompt submitted, tool finished | row marked active |
| `Stop` | row marked completed; `done` notification |

The hook does nothing outside agterm except send the notification, and never changes
Kiro's permission decision. The same file also holds the [Inbox reply
hooks](inbox.md#kiro-and-inbox-replies).

Limits: `PermissionRequest` needs Kiro CLI 3.4.0 or later; on 3.3.0 only active and completed marking works.
Sessions started with `--trust-all-tools` never ask, so they never show attention.
Restart Kiro sessions after changing hooks.
