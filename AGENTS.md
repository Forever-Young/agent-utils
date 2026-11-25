# Contributor notes

Companion utilities for agterm: configurable windows and projects, agent launch and
resume menus, coordinator workflows, Zellij tabs and optional remote integrations.
README.md covers installation; docs/guide and docs/reference cover use;
docs/internals has internals and acceptance scenarios.

## Layout

- `agent_utils/core`: agent registry, atomic writes, paths, settings, skills store,
  shared instructions, ask-agents, notifications, Zellij launcher, setup modules.
- `agent_utils/agterm`: agterm-specific modules (sessions, placement, restore, remote,
  mobile helper, Textual selector).
- `agent_utils/core/configure_tui.py`: the full-screen app behind `agents configure`.
- `agent_utils/plannotator`: optional Plannotator Inbox helpers.
- `agent_utils/cli.py`: entry points. Environment variables and hook names keep the
  `AGTERM_UTILS_*` / `agterm-utils-*` spelling.

Python 3.9+ with a standard-library core; Textual and fzf are optional.

## Rules

- Keep configuration, state and provider transcripts outside the repository. Examples
  and tests use synthetic names and portable paths.
- Document every new command, option or environment variable in `docs/reference/`;
  `tests/test_docs.py` fails on a gap.
- Run `python3 -m unittest discover -s tests` before committing.
- Never reset a registered conversation id because a terminal failed, delete provider
  history as a side effect of hiding a record, or enable a permission bypass by default.
