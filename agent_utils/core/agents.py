"""Agent registry: the supported provider CLIs and how to find their executables.

Single source for provider names. Later tools (skills, instructions, ask) extend
the entries here instead of keeping their own provider lists.
"""
import os
from pathlib import Path
import shutil

AGENT_NAMES = {'claude':'Claude','codex':'Codex','kiro-cli':'Kiro','opencode':'OpenCode',
               'cursor-agent':'Cursor','pi':'Pi','agy':'Antigravity',
               'kimi':'Kimi','qwen':'Qwen','copilot':'GitHub Copilot','vibe':'Mistral Vibe'}
AGENTS = tuple(AGENT_NAMES)


def agent_executable(agent):
    """Resolve PATH first, then Kimi's official native installer location."""
    executable=shutil.which(agent)
    if executable:return executable
    if agent=='kimi':
        candidate=Path.home()/'.kimi-code/bin/kimi'
        if candidate.is_file() and os.access(candidate,os.X_OK):return str(candidate)
    return None
