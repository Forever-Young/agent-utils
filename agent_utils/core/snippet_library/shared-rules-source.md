---
id: shared-rules-source
title: Where global agent rules live
summary: Edit ~/.config/agent-utils/instructions, then apply and check status
applies-to: all
default: on
conflicts:
---
# Shared rule: changing global agent rules

The global rules for all agents live in `~/.config/agent-utils/instructions/`
(`common.md`, plus optional per-agent parts such as `codex.md`). Edit them there,
never the per-agent copies (`~/.codex/AGENTS.md` block, `~/.pi/agent/AGENTS.md`
block, `~/.cursor/rules/agent-utils.mdc`). After any change to these files, run
`agents admin instructions apply --yes`, then `agents admin instructions status`, and report any
target that is not `ok`.
