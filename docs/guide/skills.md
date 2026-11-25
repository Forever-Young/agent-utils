# One skills store for every agent

`agents admin skills` keeps one copy of each skill that all agents should have, in
`~/.agents/skills`. Codex, OpenCode, Cursor, Pi, Kimi, Qwen, Copilot and Vibe read that
folder directly. Claude (`~/.claude/skills`), [Kiro](https://kiro.dev) (`~/.kiro/skills`) and Antigravity
(`~/.gemini/config/skills`) do not, so each of their folders gets one relative symlink
per skill. A skill meant for only some agents stays in those agents' own folders.

Every command that changes files previews until you add `--apply`.

## First sync

```sh
agents admin skills status                 # every skill: store, claude, kiro-cli, agy, owner, reader folders
agents admin skills sync                   # preview
agents admin skills sync --apply           # adopt copies into the store, back up duplicates, link
```

When the same skill exists in several places, the newest content wins (file
modification time; identical content prefers the store copy). A folder whose name
differs from its frontmatter `name` is renamed. Other copies are moved to
`~/.local/state/agent-utils/backups/<time>/<agent or store>/<skill>`. A copy in a
reader's own folder that equals the store copy is backed up too, because the agent
would load it twice. `agents admin skills link --apply` only fixes links: it creates missing ones, removes
ours where the policy excludes an agent, and removes ours whose target is gone.

Left alone:

- Skills owned by an installer are `managed`: the AWS toolkit, twg, Plannotator and
  Codex system skills.
- A store skill installed by the skills CLI (`~/.agents/.skill-lock.json`, read only)
  keeps its store copy; a differing copy in an agent folder is a `variant` and stays.

## Add, remove, exclude

```sh
agents admin skills add ~/src/my-skill --apply                  # a folder
agents admin skills add https://example.invalid/team/skills.git#my-skill --apply   # git URL#subdir
agents admin skills add ~/src/kiro-only --agents kiro --apply   # for some agents only
agents admin skills remove my-skill --apply                     # links only; --purge also moves the copies to a backup
agents admin skills exclude my-skill --agent kiro --apply       # Claude, Kiro, Antigravity only
agents admin skills include my-skill --agent kiro --apply
agents admin skills adopt my-skill --from claude --apply        # or --all
```

Rerun `add` to update a skill from its source. The policy (excludes, per-skill agent
lists, `add` sources, reviewed digests) is `~/.config/agent-utils/skills.json`.
`--project DIR` works on a project's `.agents/skills`, `.claude/skills` and
`.kiro/skills` instead.

Undo a sync: move the folder back from the backup and run `agents admin skills sync --apply`.



## Marketplace skills

Skills from Claude, Codex or Cursor plugins and Gemini extensions can be imported for
agents that cannot install from that marketplace:

```sh
agents admin skills check --all-markets            # lists marketplace skills to offer
agents admin skills import ~/.claude/plugins/.../skills/some-skill --apply
agents admin skills dismiss some-skill --apply     # stop offering one
```

An import is copied to `~/.local/share/agent-utils/skills/NAME` with a record of its
source and linked into every other installed agent's folder. `check` reports an import
whose source changed.

## Skills that use Claude-only features

`check` lists skills that use Claude-only tools (AskUserQuestion, Task,
`${CLAUDE_PLUGIN_ROOT}`, `mcp__` names, TodoWrite). The `skills-sync` skill
([skills/skills-sync](../../skills/skills-sync)) adapts them for other agents; after
reviewing, record that with `agents admin skills reviewed NAME --apply`. Install it with:

```sh
agents admin skills add skills/skills-sync --apply
```

With `skills-sync` loaded, asking any agent to "add this skill to all agents" runs `add`
or `sync`, then `check`, and adapts if needed. The `plannotator-state`, `ask-agents` and
`agent-history` skills in [skills/](../../skills) install the same way.

## Drift check and schedule

```sh
agents admin skills check                  # basic check, no model
agents admin skills check --agent codex    # Codex reviews the findings, read-only
agents admin skills check --auto           # create missing links, remove our dangling ones
agents admin skills check --inbox          # one Inbox question per finding, thread "Skills drift"
```

The basic check reports missing or dangling links, real folders that differ from the store,
copies the store lacks, entries with a wrong name or no description, orphan lock
entries and a Plannotator version outside the checked versions. `--also-project DIR`
adds projects. With `--agent` the reviewing agent proposes one action per finding (`link`, `adopt`,
`exclude`, `remove-link`, `ask-owner`); the tool validates the reply and runs actions
itself, and the agent never edits files. With `--inbox`, the next run applies your
answers ("Keep as is" silences a finding until its content changes) and sends nothing
when findings are unchanged.

```sh
agents admin skills schedule install --every 1d --agent codex --auto --inbox          # preview
agents admin skills schedule install --every 1d --agent codex --auto --inbox --apply  # launchd
agents admin skills schedule remove --apply
```

The job is `~/Library/LaunchAgents/agent-utils.skills-check.plist`. A live model run
and a real launchd load are manual acceptance steps; the tests use fakes.

## Audit a project's instructions

```sh
agents admin skills audit ~/Projects/example                 # read-only report
agents admin skills audit ~/Projects/example --agent codex   # agent proposals
agents admin skills extract ~/Projects/example --section L42 --name release-steps --apply
agents admin skills prune-rules ~/Projects/example --apply
```

`audit` reads the project's `AGENTS.md` and every agent-specific rule file
(`CLAUDE.md`, `.claude/rules/`, `AGENTS.override.md`, `.kiro/steering/`,
`.cursor/rules/`, Copilot, Gemini and Qwen files) and reports:

- sections that read like a skill (numbered steps, code, commands, a trigger such as
  "when investigating X"), with a draft name, description and pointer line;
- each agent rule file compared with `AGENTS.md` (`duplicate`, `overlaps`, `differs`,
  `own`, or `imports` for a `CLAUDE.md` with `@AGENTS.md`);
- project skill copies outside `.agents/skills`, and root skills an agent started in a
  child repository would miss (from the Claude Code, Codex and Kiro docs).

The report goes to `~/.local/state/agent-utils/audits/<project>-<hash>/<time>/`.
`extract --apply` writes `.agents/skills/NAME/SKILL.md`, links it for Claude and Kiro
and writes a patch that replaces the section with a pointer; review it, then
`git -C PROJECT apply PATCH`. `prune-rules --apply` removes lines `AGENTS.md` already
has and backs up every changed file.

Reference: [admin.md](../reference/admin.md#agents-admin-skills).
