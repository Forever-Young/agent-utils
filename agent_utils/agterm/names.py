"""One session name for the agterm sidebar and the agent's own session list.

`agents agent rename NAME` renames the agterm session (AGTERM_SESSION_ID) and,
where the agent has a route, its own session:

- codex: app-server `thread/name/set` (immediate).
- claude, qwen: a pending name that the UserPromptSubmit hook returns as
  `sessionTitle` at the next prompt (same effect as /rename; transcripts untouched).
- pi: a pending name that the agent-utils Pi extension applies after the tool call.
- opencode: `opencode api session.update`.
- kiro-cli: only the agent's own `update_session_information` tool can set it; the
  command says so.
- other agents: agterm only.

The reverse direction runs from hooks (`names.py hook AGENT EVENT`, stdin JSON):
on session start or resume the agent's saved name is copied to agterm, and on
each prompt a changed agent name is copied again. A name is copied only when the
agent's name changed since the last copy for that agterm session, so a rename
made through `agents agent rename` is not undone by a stale agent name.
Hooks never print except Claude/Qwen `sessionTitle` JSON, and always exit 0.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = 'agent_utils.agterm'

from ..core.paths import state_dir

# Session id variables set by each agent for commands its model runs. Claude and
# Qwen do not set one; their SessionStart hook exports AGENT_UTILS_*_SESSION_ID.
ENV = (('kiro-cli', 'KIRO_SESSION_ID'), ('codex', 'CODEX_THREAD_ID'), ('opencode', 'OPENCODE_SESSION_ID'),
       ('pi', 'PI_SESSION_ID'), ('copilot', 'COPILOT_AGENT_SESSION_ID'), ('cursor-agent', 'CURSOR_CONVERSATION_ID'),
       ('agy', 'ANTIGRAVITY_CONVERSATION_ID'), ('claude', 'AGENT_UTILS_CLAUDE_SESSION_ID'),
       ('qwen', 'AGENT_UTILS_QWEN_SESSION_ID'))
MARKERS = (('claude', 'CLAUDECODE'), ('qwen', 'QWEN_PID'), ('vibe', 'VIBE_SESSION'))
EXPORT = {'claude': ('CLAUDE_ENV_FILE', 'AGENT_UTILS_CLAUDE_SESSION_ID'),
          'qwen': ('QWEN_ENV_FILE', 'AGENT_UTILS_QWEN_SESSION_ID')}
PENDING_AGENTS = ('claude', 'qwen', 'pi')
MAX = 100


def detect(env=None):
    """(agent, session id or None) for the agent running this command."""
    env = os.environ if env is None else env
    for agent, var in ENV:
        if env.get(var):return agent, env[var]
    for agent, var in MARKERS:
        if env.get(var):return agent, None
    return None, None


def clean(name):
    name = ' '.join(str(name or '').split())
    if not name:raise ValueError('Session name is empty')
    if len(name) > MAX:raise ValueError('Session name is longer than %d characters' % MAX)
    if '"' in name or any(ord(c) < 32 for c in name):raise ValueError('Session name must not contain double quotes or control characters')
    return name


def _key(*parts):
    return '-'.join(re.sub(r'[^A-Za-z0-9_.-]', '_', str(p)) for p in parts if p)


def _base():
    return state_dir()/'session-names'


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.'+path.name+'.tmp')
    tmp.write_text(text);os.replace(tmp, path)


def set_pending(agent, sid, name):
    _write(_base()/'pending'/_key(agent, sid), name)


def take_pending(agent, sid):
    path = _base()/'pending'/_key(agent, sid)
    try:name = path.read_text().strip()
    except OSError:return None
    path.unlink(missing_ok=True)
    return name or None


def _seen_path(agent, sid, env):
    return _base()/'seen'/_key(agent, sid, env.get('AGTERM_SESSION_ID') or 'none')


def agterm_rename(name, env=None):
    """Rename the agterm session; None outside agterm."""
    env = os.environ if env is None else env
    target = env.get('AGTERM_SESSION_ID')
    if not target:return None
    ctl = env.get('AGTERMCTL') or shutil.which('agtermctl')
    if not ctl:return 'agtermctl not found'
    command = [ctl, 'session', 'rename', name, '--target', target]
    if env.get('AGTERM_SOCKET'):command += ['--socket', env['AGTERM_SOCKET']]
    done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
    return 'ok' if done.returncode == 0 else 'failed: '+(done.stderr or done.stdout).strip()[:200]


def sync_from_agent(agent, sid, name, env=None, force=False):
    """Copy the agent's own name to agterm when it changed (or on force)."""
    env = os.environ if env is None else env
    if not name:return None
    path = _seen_path(agent, sid, env)
    try:seen = path.read_text()
    except OSError:seen = None
    if not force and seen == name:return None
    result = agterm_rename(name, env)
    if result in ('ok', None):_write(path, name)
    return result


def _named_path(agent, sid, env):
    return _base()/'named'/_key(agent, sid, env.get('AGTERM_SESSION_ID') or 'none')


REMIND_EVERY = 8
REMIND_UNNAMED = ('Session name rule: this agterm session has not been named by you yet. Before doing anything else, '
                  'run `agents agent rename "<topic, action>"` (topic first, action last, e.g. "Login rework, implementation"; '
                  'a plan session starts with its plan prefix, e.g. "P5, ...")%s. Do not reuse the wording of the first message.')
REMIND_RECHECK = ('Session name rule: %d prompts since you last named this session ("%s"). If the topic or phase changed '
                  '(assess, implementation, review, done), run `agents agent rename "<new name>"%s` now; if not, ignore this.')
KIRO_TITLE = ' and then call your update_session_information tool with the same title (load it first if deferred)'
KIRO_TITLE_SHORT = ' and update_session_information'


def reminder(agent, sid, env):
    """Text that tells the model to name or re-check its session name, or ''."""
    if not env.get('AGTERM_SESSION_ID'):return ''
    path = _named_path(agent, sid, env)
    try:state = json.loads(path.read_text())
    except (OSError, ValueError):state = None
    kiro = agent == 'kiro-cli'
    if not state:
        return REMIND_UNNAMED % (KIRO_TITLE if kiro else '')
    state['prompts'] = int(state.get('prompts', 0)) + 1
    due = state['prompts'] >= REMIND_EVERY
    if due:state['prompts'] = 0
    _write(path, json.dumps(state))
    return REMIND_RECHECK % (REMIND_EVERY, state.get('name', ''), KIRO_TITLE_SHORT if kiro else '') if due else ''


def mark_named(agent, sid, name, env=None):
    env = os.environ if env is None else env
    _write(_named_path(agent, sid, env), json.dumps({'name': name, 'prompts': 0}))


def mark_seen(agent, sid, name, env=None):
    _write(_seen_path(agent, sid, os.environ if env is None else env), name)


def codex_set(sid, name):
    from .history import codex_server
    with codex_server() as request:request('thread/name/set', {'threadId': sid, 'name': name})


def codex_name(sid, home=None):
    """Latest name in Codex's session index (metadata only), or None."""
    home = Path(home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    found = None
    try:
        with open(home/'session_index.jsonl') as stream:
            for line in stream:
                if sid not in line:continue
                try:row = json.loads(line)
                except ValueError:continue
                if row.get('id') == sid:found = row.get('thread_name') or found
    except OSError:return None
    return found


def kiro_name(sid, home=None):
    home = Path(home or Path.home())
    for path in (home/'.kiro/sessions').glob('*/'+_key(sid)+'/session.json'):
        try:return json.loads(path.read_text()).get('title') or None
        except (OSError, ValueError):return None
    return None


def opencode_set(sid, name):
    exe = shutil.which('opencode')
    if not exe:raise ValueError('opencode not found')
    done = subprocess.run([exe, 'api', 'session.update', '--param', 'sessionID='+sid, '-d', json.dumps({'title': name})],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)
    if done.returncode:raise ValueError((done.stderr or done.stdout).strip()[:200] or 'opencode api failed')


def rename(name, agent=None, sid=None, env=None):
    """Rename both sides. Returns [(target, result)] lines for the report."""
    env = os.environ if env is None else env
    name = clean(name)
    found_agent, found_sid = detect(env)
    agent = agent or found_agent
    sid = sid or (found_sid if agent == found_agent else None)
    report = [('agterm', agterm_rename(name, env) or 'skipped: not inside agterm')]
    try:
        from ..core.zellij import rename_own_tab
        tab = rename_own_tab(name, env)
        if tab:report.append(('zellij', tab))
    except Exception as exc:
        report.append(('zellij', 'failed: %s' % exc))
    if not agent:
        report.append(('agent', 'skipped: no agent detected (pass --agent)'));return report
    if agent == 'kiro-cli':
        report.append((agent, 'call your update_session_information tool with title "%s"' % name))
    elif not sid and agent in ('codex', 'opencode', *PENDING_AGENTS):
        hint = ' (the agent-utils session-name hooks are not active; run scripts/configure-session-names.py)' if agent in EXPORT else ''
        report.append((agent, 'skipped: session id unknown'+hint))
    elif agent == 'codex':
        try:codex_set(sid, name);report.append((agent, 'ok'))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:report.append((agent, 'failed: '+str(exc)))
    elif agent == 'opencode':
        try:opencode_set(sid, name);report.append((agent, 'ok'))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:report.append((agent, 'failed: '+str(exc)))
    elif agent in PENDING_AGENTS:
        set_pending(agent, sid, name)
        report.append((agent, 'pending: applied '+('after this tool call' if agent == 'pi' else 'at the next prompt')))
    else:
        report.append((agent, 'skipped: %s has no way to set its own session name; agterm only' % agent))
    if sid:mark_seen(agent, sid, name, env);mark_named(agent, sid, name, env)
    return report


def hook(agent, event, data, env=None):
    """Hook body; returns the text to print (usually empty)."""
    env = os.environ if env is None else env
    sid = data.get('session_id') or (env.get('KIRO_SESSION_ID') if agent == 'kiro-cli' else None)
    if not sid:return ''
    start = event == 'start'
    if start and agent in EXPORT:
        file_var, var = EXPORT[agent]
        if env.get(file_var):
            with open(env[file_var], 'a') as stream:stream.write('export %s=%s\n' % (var, _key(sid)))
    note = '' if start else reminder(agent, _key(sid), env)
    if agent in ('claude', 'qwen'):
        pending = take_pending(agent, sid)
        if pending:
            mark_seen(agent, sid, pending, env)
            name = 'SessionStart' if start else 'UserPromptSubmit'
            return json.dumps({'hookSpecificOutput': {'hookEventName': name, 'sessionTitle': pending}})
        sync_from_agent(agent, sid, data.get('session_title'), env, force=start)
        if note:return json.dumps({'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': note}})
        return ''
    elif agent == 'codex':
        sync_from_agent(agent, sid, codex_name(sid), env, force=start)
    elif agent == 'kiro-cli':
        sync_from_agent(agent, sid, kiro_name(sid), env, force=start)
    return note


def command(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='agents agent', description='One session name for agterm and the agent')
    sub = parser.add_subparsers(dest='operation', required=True)
    r = sub.add_parser('rename', help='rename the agterm session and the agent\'s own session')
    r.add_argument('name');r.add_argument('--agent');r.add_argument('--session')
    s = sub.add_parser('sync', help='copy a name (or the agent\'s saved name) to the agterm session')
    s.add_argument('--agent');s.add_argument('--session');s.add_argument('--name')
    args = parser.parse_args(argv)
    if args.operation == 'rename':
        report = rename(args.name, args.agent, args.session)
        for target, result in report:print('%-8s %s' % (target, result))
        return 1 if any(str(r).startswith('failed') for _, r in report) else 0
    agent, sid = detect()
    agent = args.agent or agent;sid = args.session or sid
    name = args.name
    if not name and sid and agent in ('codex', 'kiro-cli'):name = (codex_name if agent == 'codex' else kiro_name)(sid)
    if not name:print('No saved agent name to copy');return 1
    result = sync_from_agent(agent or 'unknown', sid or 'none', clean(name), force=True) or 'skipped: not inside agterm'
    print('agterm   '+result)
    return 0 if result == 'ok' or result.startswith('skipped') else 1


def main(argv):
    if len(argv) < 4 or argv[1] != 'hook':return
    try:data = json.loads(sys.stdin.read() or '{}')
    except ValueError:data = {}
    out = hook(argv[2], argv[3], data if isinstance(data, dict) else {})
    if out:print(out)


if __name__ == '__main__':
    try:main(sys.argv)
    except BaseException:pass
    sys.exit(0)
