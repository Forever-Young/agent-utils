# Claude Code rules and project memory

Find a local project's existing Claude guidance instead of guessing its preferences.

## Project and scope

Resolve the project name to its checkout. A workspace can hold several independent
repositories: identify the workspace root and the repository being changed, and do
not apply a sibling repository's conventions to the whole workspace. Your own
agent's instructions keep their priority; Claude's files are added context.

## Rule sources

Report absent files accurately.

- Global: `~/.claude/CLAUDE.md` and Markdown files under `~/.claude/rules/`.
- Project: `CLAUDE.md`, `.claude/CLAUDE.md`, `CLAUDE.local.md` and
  `.claude/rules/**/*.md` in the repository, the workspace and relevant ancestor
  directories, following directory-specific files down to the code being changed.
- Imports: follow `@path` lines, resolved relative to the file that contains them
  (a file containing only `@AGENTS.md` imports that repository's `AGENTS.md`). Track
  visited files to avoid cycles.

To include ignored local files, search with narrow globs, for example
`rg --files --hidden --no-ignore -g 'CLAUDE*.md' -g '.claude/**'`, excluding `.git`,
dependency folders and virtual environments. Check ancestor directories explicitly;
a search rooted in the repository cannot see them. Do not scan the whole home folder.

Read rule frontmatter first and keep its path restrictions and conditions. When
summarizing, name the source of each rule and separate global, workspace and
repository guidance.

## Saved project memory

Memory lives in `~/.claude/projects/<key>/memory/`. The key is usually the absolute
working path with `/` replaced by `-` (`/home/user/projects/example` becomes
`-home-user-projects-example`). That is a hint, not a guaranteed mapping: confirm an
ambiguous match with the `cwd` field of transcripts (`*.jsonl`) in the same folder.
Worktrees and nested repositories can have their own keys; check the workspace key
too. Do not merge memories of unrelated checkouts with similar names.

Read `memory/MEMORY.md` first, then the files it references that matter for the
task (all of them when the user asks for every project rule). Separate preferences
and feedback (naming, branching, PR style, test scope, tools) from reference context
(architecture, data locations, incidents). Memory can be stale: keep dates and
verify operational facts before treating them as current. An absent memory folder
does not mean the project has no instructions.

## Conversations

Claude Code transcripts are the `*.jsonl` files beside `memory/`. When a
`claude-history` MCP server is available, prefer its `list_sessions`,
`search_history` and `get_session` tools with a project filter and bounded results.
It does not expose `CLAUDE.md` or memory; read those files directly. Cloud-only
sessions and Claude Desktop project instructions are not in these files (see
`claude-desktop.md`).

## Using the result

Apply relevant preferences to the user's authorized work in that project. If sources
conflict, report it and prefer the current user's direction and the rules of the
specific repository. Do not turn one repository's test command into a workspace-wide
rule.
