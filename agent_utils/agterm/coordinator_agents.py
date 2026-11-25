"""Coordinator launch pieces for Codex and Kiro (Claude's live in cli.agent_argv).

Codex: dispatcher prompt via `-c developer_instructions`, tool gate via a per-launch
`-c hooks.PreToolUse`; nothing global is edited. Kiro: a generated custom agent
(`agterm-coordinator`) with the prompt, a tool allowlist and a preToolUse hook, written
to the Kiro agents directory when the coordinator is launched.
"""
import json
from pathlib import Path
import shlex
import sys

from ..core.files import atomic_write

KIRO_AGENT = 'agterm-coordinator'
GATE = Path(__file__).with_name('coordinator_gate.py')
DISPATCH_COMMANDS = ['agents agterm .*', 'agents agent .*', 'agtermctl .*', 'pbpaste', 'pbcopy.*', 'ls.*', 'pwd', 'date']


def gate_command(fmt=None):
    words = [sys.executable, str(GATE)] + (['--format', fmt] if fmt else [])
    return shlex.join(words)


def toml_str(text):
    return json.dumps(text)


def codex_args(prompt):
    hook = ('[{matcher="*",hooks=[{type="command",command=%s,timeout=10}]}]' % toml_str(gate_command()))
    return ['-c', 'developer_instructions=' + toml_str(prompt), '-c', 'hooks.PreToolUse=' + hook]


def kiro_config(prompt):
    return {
        'name': KIRO_AGENT,
        'description': 'agent-utils coordinator: opens and messages worker sessions, does no work itself.',
        'prompt': prompt,
        'tools': ['execute_bash', 'fs_read', 'fs_write'],
        'allowedTools': ['fs_read'],
        'toolsSettings': {
            'execute_bash': {'allowedCommands': DISPATCH_COMMANDS},
            'fs_write': {'allowedPaths': ['tasks/**', 'tmp/**']},
        },
        'hooks': {'preToolUse': [{'matcher': '*', 'command': gate_command('kiro')}]},
    }


def kiro_agent_path(home=None):
    return Path(home or Path.home()) / '.kiro' / 'agents' / (KIRO_AGENT + '.json')


def write_kiro_agent(prompt, home=None):
    """Write the agent file when it differs; return its path."""
    path = kiro_agent_path(home)
    config = kiro_config(prompt)
    if not path.exists() or json.loads(path.read_text()) != config:
        atomic_write(path, config)
    return path
