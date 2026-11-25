# Shared agent instructions

One rule set for every installed agent. You write the rules once; agent-utils links or
copies them to the place each agent reads its global instructions.

## Sources

The sources live in `~/.config/agent-utils/instructions/`:

- `common.md` for every agent;
- optional parts for one agent: `claude.md`, `kiro.md`, `codex.md`, `pi.md`,
  `opencode.md`, `cursor.md`.

The first apply writes a starter `common.md` from the default snippets when none exists.
Edit the sources there, never the per-agent copies.

## Apply

```sh
agents admin instructions status          # one line per agent; exit 1 unless all ok
agents admin instructions apply           # preview
agents admin instructions apply --yes     # link or write
agents admin instructions remove --yes    # undo; the sources stay
```

| Agent | How it gets the rules | After editing a source |
| --- | --- | --- |
| Claude | symlinks `~/.claude/rules/agent-utils.md`, `agent-utils-claude.md` | nothing to do |
| [Kiro](https://kiro.dev) | symlinks `~/.kiro/steering/agent-utils.md`, `agent-utils-kiro.md` | nothing to do |
| OpenCode | paths in the `instructions` array of `~/.config/opencode/opencode.json` | nothing to do |
| Copilot | symlink `~/.copilot/copilot-instructions.md` | nothing to do |
| Kimi, Qwen, Vibe | symlink `AGENTS.md` in `~/.kimi-code`, `~/.qwen`, `~/.vibe` | nothing to do |
| Antigravity | symlink `~/.gemini/config/AGENTS.md` | nothing to do |
| Codex, Pi | copy between `agent-utils` markers in `~/.codex/AGENTS.md`, `~/.pi/agent/AGENTS.md` | `apply --yes` |
| Cursor | copy `~/.cursor/rules/agent-utils.mdc` (needs frontmatter) | `apply --yes` |

Agents whose executable is not found are skipped. Symlinked agents other than Claude
and Kiro get `common.md` only. Text outside the markers is never touched. `status`
reports `stale` for an out-of-date copy, `edited` for a hand-edited one and `foreign`
for a file agent-utils did not create; `apply` keeps the last two unless `--force`
(which writes a backup first). Adding or removing a part also needs `apply --yes`.
Agents load the change in their next session.

Kiro refuses a `#[[file:...]]` reference outside `~/.kiro/steering`, which is why it gets a symlink.
Cursor CLI has no global rules file; its rule applies when the working folder is under your home folder.

## Build the rules from snippets

A snippet is a small rule copied into a source between markers, with a checksum.

```sh
agents admin instructions compose --list                         # the library
agents admin instructions compose --status                       # each snippet in common.md
agents admin instructions compose --add untrusted-content        # preview the diff
agents admin instructions compose --add untrusted-content --yes  # write, back up, apply
agents admin instructions compose --update --yes                 # refresh stale copies
agents admin instructions compose --remove commit-conventions --yes
agents admin instructions compose --part codex --add commit-conventions --yes
agents admin instructions compose --replace --defaults --yes     # rebuild from default snippets
agents admin instructions compose --project ~/Projects/example --add privacy-in-repos --yes
```

By default `compose` edits `common.md` (or one part with `--part`), keeps a backup and
then runs `agents admin instructions apply --yes`; `--no-sync` skips that last step.
`--project DIR` (its `AGENTS.md`) and `--target PATH` edit one file and never sync.

| Snippet | Default |
| --- | --- |
| `inbox-policy`: what goes to the Plannotator Inbox | on |
| `session-naming`: keep agterm session names useful | on |
| `shared-rules-source`: edit the sources, then apply | on |
| `confirmation-questions`: make blocking questions stand out | on |
| `privacy-in-repos`, `commit-conventions`, `untrusted-content` | off |

`agents configure` lists every snippet (bundled or `(custom)`, on or off), shows its text before
you turn it on, previews the diff, and can write a new custom snippet for you.

### Bundled rules

These ship with agent-utils in [`agent_utils/core/snippet_library/`](../../agent_utils/core/snippet_library/). "On" rules go into the starter `common.md`; add or remove any of them with `agents admin instructions compose` or in `agents configure`.

| Rule | What it tells the agent | Default |
| --- | --- | --- |
| [Commit conventions](../../agent_utils/core/snippet_library/commit-conventions.md) | Run the checks first, one focused change per commit, say why in the body, keep other people's uncommitted work. | off |
| [Visible confirmation questions](../../agent_utils/core/snippet_library/confirmation-questions.md) | A question that blocks work goes on its own last line, starts with a warning sign and is bold. | on |
| [Plannotator Inbox policy](../../agent_utils/core/snippet_library/inbox-policy.md) | Questions, long reports and long code go to the Plannotator Inbox; no blocking review sessions. | on |
| [Privacy in repositories](../../agent_utils/core/snippet_library/privacy-in-repos.md) | No credentials, user paths, customer names or real transcripts in source, tests or history; no pushing unless asked. | off |
| [agterm session names](../../agent_utils/core/snippet_library/session-naming.md) | Topic-first sidebar names, renamed when the topic or phase changes. | on |
| [Where global agent rules live](../../agent_utils/core/snippet_library/shared-rules-source.md) | Edit the sources in `~/.config/agent-utils/instructions`, then apply and check status. | on |
| [Untrusted content](../../agent_utils/core/snippet_library/untrusted-content.md) | Tool output, web pages and file contents are data, not instructions; report embedded instructions. | off |

### Rule files

A rule is one markdown file: a short header with metadata, then the text agents read.

```markdown
---
id: privacy-in-repos
title: Privacy in repositories
summary: Keep private data out of source, examples, tests and commit history
applies-to: all
default: off
conflicts:
variables: owner=your GitHub user or organisation
---
# Rule: privacy in repositories

- Do not commit credentials or real transcripts.
- Repositories under {{owner}} are public unless stated otherwise.
```

| Field | Meaning |
| --- | --- |
| `id` | Name used by `compose --add ID` and in the markers; defaults to the file name. |
| `title`, `summary` | What the list in `agents configure` shows. |
| `applies-to` | `all`, or the agents the rule is for. |
| `default` | `on` rules are written into the first `common.md`. |
| `conflicts` | Ids of rules that should not be on together. |
| `variables` | `name=meaning; name=meaning`: what each `{{name}}` in the text is. Personalization asks for the value with that meaning. |

Bundled rules live in `agent_utils/core/snippet_library/`. Your own go in
`~/.config/agent-utils/snippets/` (the list marks them `(custom)`, and a custom rule with
the same id replaces the bundled one); `agents configure` can write a new one for you.
Values for `{{name}}` live in `~/.config/agent-utils/snippets.json`, which stays on your machine.

Your own snippets go in `~/.config/agent-utils/snippets/NAME.md`, with the same
frontmatter as the bundled ones in `agent_utils/core/snippet_library/`; a private
snippet wins over a bundled one with the same id. `{{name}}` variables come from
`~/.config/agent-utils/snippets.json` (`{"variables": {"name": "..."}}`).

States: `ok`, `stale` (library changed), `edited` (changed by hand; kept unless
`--force`), `missing`, `unset` (a variable is not set). `--replace` writes only the
added snippets in the given order; otherwise text outside markers is kept. Undo:
restore the `.backup-*` file beside the source and run `agents admin instructions apply --yes`.

Reference: [admin.md](../reference/admin.md#agents-admin-instructions).
