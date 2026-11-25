---
id: commit-conventions
title: Commit conventions
summary: Small focused commits after checks pass
applies-to: all
default: off
conflicts:
---
# Rule: commits

- Run the project's checks first. Commit only when they pass.
- One focused change per commit. Subject in the imperative, about 70 characters, no trailing period.
- The body says why, not what the diff already shows.
- Check for uncommitted changes you did not make before editing, and do not discard them.
