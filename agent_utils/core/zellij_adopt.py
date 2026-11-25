"""Adopt an agent started by hand in a Zellij pane.

`--self` (from inside the pane, or from an agent's SessionStart hook) is exact: the pane is
ZELLIJ_PANE_ID and the provider is the nearest ancestor process whose executable is a known agent.
`--pane N` from elsewhere matches the pane's foreground command and folder to one unmanaged provider
process; it adopts only when exactly one process matches. Nothing is typed into panes and no transcript
is touched; the only writes are our registry and that pane's resurrect command.
"""
import os
from pathlib import Path
import re
import subprocess
import uuid

from .agents import AGENTS

# Flags that carry the conversation id in a provider's own command line.
ID_FLAGS = {'claude': ('--session-id', '--resume', '-r'), 'kiro-cli': ('--resume-id',), 'opencode': ('--session', '-s'),
            'pi': ('--session',), 'qwen': ('--resume',), 'cursor-agent': ('--resume',), 'kimi': ('--session',),
            'copilot': ('--resume',), 'agy': ('--conversation',), 'vibe': ('--resume',)}


def process_table():
    """{pid: (ppid, args)} for all processes (read-only `ps`)."""
    out = subprocess.run(['ps', '-axww', '-o', 'pid=,ppid=,args='], capture_output=True, text=True, timeout=10).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2] if len(parts) > 2 else '')
    return table


def agent_of(args):
    """The agent whose executable this command line runs, else None. `node .../claude` counts too."""
    words = args.split()
    if not words:return None
    for word in words[:2]:
        base = os.path.basename(word)
        if base in AGENTS:return base
        if base == 'kiro-cli-chat':return 'kiro-cli'
    return None


def provider_ancestor(start, table):
    """(pid, agent, args) of the nearest ancestor of `start` (itself included) that is an agent."""
    pid = start
    for _ in range(24):
        if pid not in table:return None
        ppid, args = table[pid]
        agent = agent_of(args)
        if agent:
            # The launcher can exec a helper of the same agent; keep the outermost one of a chain.
            while ppid in table and agent_of(table[ppid][1]) == agent:
                pid, (ppid, args) = ppid, table[ppid]
            return pid, agent, args
        pid = ppid
    return None


def under_zellij(pid, table):
    for _ in range(32):
        if pid not in table:return False
        ppid, args = table[pid]
        if re.search(r'(^|/)zellij\s+--server\b', args):return True
        pid = ppid
    return False


def conversation_from_args(agent, args):
    words = args.split()
    if agent == 'codex':
        for i, w in enumerate(words[:-1]):
            if w == 'resume' and not words[i + 1].startswith('-'):return words[i + 1]
        return None
    for i, w in enumerate(words):
        for flag in ID_FLAGS.get(agent, ()):
            if w == flag and i + 1 < len(words) and not words[i + 1].startswith('-'):return words[i + 1]
            if w.startswith(flag + '='):return w.split('=', 1)[1]
    return None


def process_cwd(pid):
    try:
        out = subprocess.run(['lsof', '-a', '-d', 'cwd', '-p', str(pid), '-Fn'], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):return None
    for line in out.splitlines():
        if line.startswith('n/'):return line[1:]
    return None


def resolve_project(cwd, config):
    try:
        from ..cli import load
        from ..agterm.startup import resolve
        found = resolve(load(config), {}, None, None, cwd)
    except (OSError, ValueError):found = None
    return found


def managed_pids():
    from ..agterm.sessions import records
    return {int(r['provider_pid']) for r in records().values() if r.get('provider_pid') and not r.get('exited_at')}


def adopt(z, pane_id, tab_id, pid, agent, args, cwd, config, conversation=None, rename=False, birth=None):
    """Create the record, set the resurrect command, return the record."""
    from ..agterm.runner import wrapped
    from ..agterm.restore import resume_argv, discover
    from ..agterm.sessions import locked_registry, now
    from ..agterm.workers import process_birth
    birth = birth or process_birth(pid)
    conversation = conversation or conversation_from_args(agent, args)
    if not conversation and agent in ('codex', 'kiro-cli'):
        conversation = discover({'agent': agent, 'provider_pid': pid, 'provider_birth': birth})
    found = resolve_project(cwd, config)
    window, project = found if found else ({'name': 'manual'}, {'id': 'manual', 'name': os.path.basename(cwd) or cwd, 'roots': [cwd]})
    key = str(uuid.uuid4())
    record = {'id': key, 'project': project['id'], 'project_name': project['name'], 'roots': project['roots'] if found else [cwd],
              'agent': agent, 'title': project.get('session_title', project['name']), 'agent_args': project.get('agent_args', {}),
              'conversation_id': conversation, 'created_at': now(), 'updated_at': now(), 'hidden': False, 'archived': False,
              'status': 'adopted', 'cwd': cwd, 'window': window['name'], 'provider_pid': pid, 'provider_birth': birth,
              'aliases': [], 'prs': [], 'branches': [], 'repos': [], 'task': None,
              'zellij': {'session': z.session, 'tab_id': tab_id, 'pane_id': pane_id, 'resumable': False}}
    with locked_registry() as registry:
        sessions = registry.setdefault('sessions', {})
        for r in sessions.values():
            zr = r.get('zellij') or {}
            same_pane = zr.get('session') == z.session and zr.get('pane_id') == pane_id and not r.get('exited_at')
            same_conv = conversation and r.get('conversation_id') == conversation and not r.get('exited_at') and not r.get('archived')
            if same_pane or same_conv or (r.get('provider_pid') == pid and not r.get('exited_at')):
                raise ValueError('Already managed by record %s' % r['id'][:8])
        sessions[key] = record
    argv = resume_argv(record, config) if conversation else None
    if argv and z.set_resurrect(pane_id, wrapped(key, argv)):
        from ..agterm.runner import update
        record['zellij']['resumable'] = True
        update(key, zellij=record['zellij'])
    if rename and tab_id is not None:
        try:z.rename(tab_id, '%s: %s' % (project['name'], agent))
        except Exception:pass
    return record


def adopt_self(config, env=None, conversation=None, rename=False, agent=None):
    """Adopt the agent this process runs under. Returns (record or None, message)."""
    from .zellij import Zellij, in_zellij, locate_self
    env = os.environ if env is None else env
    if not in_zellij(env):return None, 'not inside a Zellij pane'
    table = process_table()
    found = provider_ancestor(os.getppid(), table)
    if not found:return None, 'no agent process above this command'
    pid, found_agent, args = found
    # Started by agents: the runner is the provider's parent. (AGTERM_UTILS_RECORD in the environment is
    # not proof: a Zellij server started from a managed session passes it to every pane.)
    if 'agterm/runner.py' in table.get(table[pid][0], (0, ''))[1]:return None, 'already managed (started by agents)'
    if agent and agent != found_agent:return None, 'the agent above this command is %s, not %s' % (found_agent, agent)
    if not conversation:
        from ..agterm.names import detect
        detected, sid = detect(env)
        if detected == found_agent:conversation = sid
    z = Zellij(env=env)
    where = locate_self(env, z)
    if not where:return None, 'this pane is not in the Zellij pane list'
    cwd = process_cwd(pid) or os.getcwd()
    record = adopt(z, where[2], where[1], pid, found_agent, args, cwd, config, conversation, rename)
    return record, describe(record)


def describe(record):
    resumable = record['zellij'].get('resumable')
    return 'Adopted %s in %s as %s%s' % (record['agent'], record['project_name'], record['id'][:8],
                                         ', resumes after a Zellij restart' if resumable else
                                         ', no conversation id found: it will not resume after a restart')


def candidates(pane, table, managed):
    """Unmanaged agent processes under a Zellij server that match the pane's command and folder."""
    command = (pane.get('terminal_command') or pane.get('pane_command') or '').strip()
    agent = agent_of(command)
    if not agent:return agent, []
    cwd = pane.get('pane_cwd')
    out = []
    for pid, (ppid, args) in table.items():
        if pid in managed or agent_of(args) != agent or agent_of(table.get(ppid, (0, ''))[1]) == agent:continue
        if not under_zellij(pid, table):continue
        if ' '.join(args.split()[1:]) != ' '.join(command.split()[1:]):continue
        pcwd = process_cwd(pid)
        if cwd and pcwd and os.path.realpath(pcwd) != os.path.realpath(cwd):continue
        out.append((pid, args, pcwd or cwd))
    return agent, out


def adoptable(z):
    """Panes running an agent with no record: [(pane, agent)]."""
    from .zellij import tab_records
    known = {(r['zellij'].get('session'), r['zellij'].get('pane_id')) for r in tab_records(z).values()}
    managed_tabs = set(tab_records(z))
    out = []
    for pane in z.panes():
        agent = agent_of((pane.get('terminal_command') or '').strip())
        if agent and (z.session, pane['id']) not in known and pane.get('tab_id') not in managed_tabs:
            out.append((pane, agent))
    return out


def adopt_pane(z, pane_id, config, conversation=None, rename=False):
    pane = next((p for p in z.panes() if p['id'] == pane_id), None)
    if not pane:raise ValueError('No terminal pane %s in session %s' % (pane_id, z.session))
    table = process_table()
    agent, found = candidates(pane, table, managed_pids())
    if not agent:raise ValueError('Pane %s does not run a known agent (it runs %r)' % (pane_id, pane.get('terminal_command')))
    if len(found) != 1:
        hint = ' Run `agents zellij adopt --self` inside that pane (for example `!agents zellij adopt --self` in the agent).'
        if not found:raise ValueError('No unmanaged %s process matches pane %s.%s' % (agent, pane_id, hint))
        raise ValueError('%d %s processes match pane %s (pids %s); not guessing.%s' % (
            len(found), agent, pane_id, ', '.join(str(f[0]) for f in found), hint))
    pid, args, cwd = found[0]
    return adopt(z, pane_id, pane.get('tab_id'), pid, agent, args, cwd, config, conversation, rename)
