"""Skills drift check: `agents admin skills check` and `agents admin skills schedule`.

The basic check (no model) finds drift between the store, the agent folders and projects.
The agent review (optional, `--agent`) asks one agent, read-only, to classify the findings and
propose actions from a closed set; this tool validates the reply and runs the actions
itself. `--auto` applies only safe actions: create a missing link, remove our own
dangling link. Nothing is overwritten or deleted. `--inbox` posts one question per
unresolved finding to the thread "Skills drift"; the next run applies the answers.
Unchanged findings send nothing.
"""
import argparse
import fcntl
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import skills
from .files import atomic_write
from .paths import state_dir

THREAD = 'Skills drift'
ACTIONS = ('link', 'adopt', 'exclude', 'remove-link', 'ask-owner')
CLASSES = ('shared', 'agent-specific', 'plugin-managed', 'duplicate', 'obsolete', 'unclear')
SAFE = {'missing': 'link', 'broken-link': 'remove-link'}
# kind -> actions the tool can run for it (ask-owner is always allowed)
APPLICABLE = {'missing': ('link', 'exclude'), 'broken-link': ('remove-link',), 'unwanted-link': ('remove-link',),
              'drift': ('adopt', 'exclude'), 'real-dir': ('adopt', 'exclude'), 'unshared': ('adopt',)}
# kind -> [(choice text, action)]; the first choice is the default recommendation.
CHOICES = {
    'missing': [('Link it for {agent}', 'link'), ('Exclude it for {agent}', 'exclude'), ('Keep as is', 'keep')],
    'broken-link': [('Remove the dangling link', 'remove-link'), ('Keep as is', 'keep')],
    'unwanted-link': [('Remove the link', 'remove-link'), ('Keep as is', 'keep')],
    'drift': [("Adopt {agent}'s copy into the store", 'adopt'), ('Exclude it for {agent}', 'exclude'), ('Keep as is', 'keep')],
    'real-dir': [('Adopt it into the store and link', 'adopt'), ('Exclude it for {agent}', 'exclude'), ('Keep as is', 'keep')],
    'unshared': [('Adopt it into the store for every agent', 'adopt'), ('Keep as agent-specific', 'keep')],
    'offer': [('Import it for agents that lack it', 'import'), ("Don't offer it again", 'dismiss')],
}
FYI = ('store-broken', 'name-mismatch', 'no-description', 'orphan-lock', 'plannotator-recipes', 'adapt', 'stale')
MAX_QUESTIONS = 10
ID_MARK = re.compile(r'`(skill-check:[^`]+)`')


def _fp(*parts):
    return hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()[:12]


def _scope(env):
    return str(env.project) if env.project else 'user'


def _finding(env, kind, skill, agent='', path='', detail='', **extra):
    f = {'id': 'skill-check:%s:%s:%s:%s' % (kind, _scope(env), skill, agent), 'kind': kind, 'scope': _scope(env),
         'skill': skill, 'agent': agent, 'path': str(path), 'detail': detail}
    f['fingerprint'] = _fp(kind, skill, agent, detail)
    f.update(extra)
    return f


def _description(path):
    try: text = (Path(path)/'SKILL.md').read_text()
    except (OSError, UnicodeDecodeError): return None
    match = re.match(r'---\n(.*?)\n---', text, re.S)
    found = match and re.search(r'^description:\s*(\S.*)$', match.group(1), re.M)
    if found: return found.group(1).strip()
    return 'block' if match and re.search(r'^description:\s*$', match.group(1), re.M) else None


def stage_a(env, plannotator_version=None, offers=True, all_markets=False, reports=None):
    """Findings for one environment (the user's home or one project)."""
    found = []
    for row in skills.status(env):
        name = row['name']; stored = env.store/name
        if row['store'] == 'broken':
            found.append(_finding(env, 'store-broken', name, path=stored, detail='store entry is a dangling link'))
        for agent, state in row['agents'].items():
            path = env.dirs[agent]/name
            if state == 'missing': found.append(_finding(env, 'missing', name, agent, path))
            elif state == 'broken': found.append(_finding(env, 'broken-link', name, agent, path, 'link target is gone'))
            elif state == 'excluded-linked': found.append(_finding(env, 'unwanted-link', name, agent, path, 'excluded by policy'))
            elif state == 'differs':
                found.append(_finding(env, 'drift', name, agent, path, 'copy %s, store %s' % (skills.tree_digest(path), skills.tree_digest(stored))))
            elif state == 'same': found.append(_finding(env, 'real-dir', name, agent, path, 'identical to the store copy'))
            elif state == 'only-here':
                found.append(_finding(env, 'unshared', name, agent, path, 'copy %s' % skills.tree_digest(path)))
    for name in skills._listed(env.store):
        stored = env.store/name
        if not stored.exists() or skills.managed(name, stored): continue
        front = skills.front_name(stored)
        if front and front != name: found.append(_finding(env, 'name-mismatch', name, path=stored, detail='frontmatter name %s' % front))
        if _description(stored) is None: found.append(_finding(env, 'no-description', name, path=stored, detail='no description'))
    if not env.project:
        lock = env.home/skills.LOCK_REL
        try: entries = json.loads(lock.read_text()).get('skills') or {} if lock.is_file() else {}
        except ValueError: entries = {}
        for name in sorted(entries):
            if not (env.store/name).exists() and not (env.store/name).is_symlink():
                found.append(_finding(env, 'orphan-lock', name, path=lock, detail='lock entry without a store skill'))
        version = plannotator_version() if plannotator_version else None
        if version:
            from ..plannotator.inbox import CHECKED_VERSIONS
            if version not in CHECKED_VERSIONS:
                found.append(_finding(env, 'plannotator-recipes', 'plannotator', detail='installed %s, checked %s' % (version, ', '.join(CHECKED_VERSIONS))))
    report = skills.check(env, all_markets)
    if reports is not None: reports[_scope(env)] = report
    for a in report['adapt']: found.append(_finding(env, 'adapt', a['skill'], path=a['path'], detail=json.dumps(a['uses'], sort_keys=True)))
    for s in report['stale']: found.append(_finding(env, 'stale', s['skill'], path=s['path'], detail=s['reason']))
    if offers:
        for o in report['offer']:
            found.append(_finding(env, 'offer', o['skill'], path=o['source'], detail='%s/%s' % (o['market'], o['plugin']), source=o['source']))
    return found


def default_action(f):
    return SAFE.get(f['kind']) or ('ask-owner')


# ---------- Agent review ----------

def judge_prompt(items):
    brief = [{'id': f['id'], 'kind': f['kind'], 'skill': f['skill'], 'agent': f['agent'], 'detail': f['detail'],
              'description': _description(f['path']) if f['path'] and Path(f['path']).is_dir() else None,
              'allowed_actions': list(APPLICABLE.get(f['kind'], ()))+['ask-owner']} for f in items]
    return ('You judge drift between coding agents\' skill folders. Read-only: change nothing, run no commands that write.\n'
            'For each finding below classify it as one of %s and propose one action from its allowed_actions '
            '(%s). `link` shares a store skill with an agent, `adopt` moves a copy into the shared store, `exclude` '
            'hides a skill from one agent, `remove-link` drops a dangling link, `ask-owner` leaves it to the owner. '
            'Prefer ask-owner when unsure.\nReply with only a JSON array: '
            '[{"id": "<finding id>", "class": "...", "action": "...", "reason": "one short sentence"}].\n\nFindings:\n%s\n'
            % (', '.join(CLASSES), ', '.join(ACTIONS), json.dumps(brief, indent=1)))


def parse_judgement(text, items):
    """Validated {finding id: {class, action, reason}}; anything off schema is dropped."""
    match = re.search(r'\[.*\]', text or '', re.S)
    try: data = json.loads(match.group(0)) if match else []
    except ValueError: data = []
    known = {f['id']: f for f in items}; out = {}
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict) or row.get('id') not in known: continue
        f = known[row['id']]; action = row.get('action')
        if row.get('class') not in CLASSES or action not in ACTIONS: continue
        if action != 'ask-owner' and action not in APPLICABLE.get(f['kind'], ()): continue
        out[f['id']] = {'class': row['class'], 'action': action, 'reason': str(row.get('reason', ''))[:200]}
    return out


def pick_agent(candidates, input_fn=input):
    if not candidates: raise ValueError('No agent CLI installed to judge with')
    for i, name in enumerate(candidates, 1): print('  %d. %s' % (i, name))
    choice = input_fn('Judge with which agent? ').strip()
    if choice.isdigit() and 1 <= int(choice) <= len(candidates): return candidates[int(choice)-1]
    if choice in candidates: return choice
    raise ValueError('No such agent: %r' % choice)


def judge(items, agent, ask_fn=None, timeout=600, cwd=None):
    if ask_fn is None:
        from .ask import ask as ask_fn
    run = ask_fn(agent, judge_prompt(items), cwd=str(cwd or Path.home()), timeout=timeout)
    result = (run.get('results') or [{}])[0]
    if result.get('status') != 'ok': raise ValueError('%s did not answer (%s)' % (agent, result.get('status', 'no result')))
    return parse_judgement(Path(result['answer']).read_text(), items)


# ---------- actions ----------

def apply_action(env, f, action):
    """Run one closed-set action for finding F; returns the action records."""
    name, agent = f['skill'], f['agent']
    if action == 'link': return skills.link(env, only=[agent], dry=False, names=[name])
    if action == 'remove-link':
        path = Path(f['path'])
        if not path.is_symlink() or (path.exists() and not skills._ours(path, env.store/name) and f['kind'] != 'unwanted-link'):
            return [{'action': 'skip', 'skill': name, 'agent': agent, 'path': f['path'], 'reason': 'not our link'}]
        path.unlink(); return [{'action': 'unlink', 'skill': name, 'agent': agent, 'path': f['path']}]
    if action == 'adopt': return skills.adopt(env, name, agent or None, dry=False)
    if action == 'exclude': return skills.set_policy(env, name, agent, True, dry=False)
    if action == 'import': return skills.import_skill(env, f['source'], name, None, dry=False)
    if action == 'dismiss': return skills.dismiss(env, [name], dry=False)
    raise ValueError('Unknown action %r' % action)


# ---------- state and Inbox ----------

def state_path(base=None):
    return Path(base or state_dir())/'skills-check.json'


def load_state(base=None):
    try: data = json.loads(state_path(base).read_text())
    except (OSError, ValueError): data = {}
    data.setdefault('decided', {}); data.setdefault('handled', []); return data


def choices_for(f, env):
    rows = CHOICES.get(f['kind'], [])
    if env.project or not env.policy_path:
        rows = [r for r in rows if r[1] not in ('exclude', 'dismiss', 'import')]
    return [(text.format(agent=f['agent']), act) for text, act in rows]


def question(f, env, proposal=None):
    rows = choices_for(f, env)
    if len(rows) < 2: return None
    rec = rows[0][0]
    if proposal:
        rec = next((t for t, a in rows if a == proposal['action']), rec)
    text = '`%s` %s%s%s. What should happen?\n\n%s\n\nRecommended: %s' % (
        f['id'], f['kind'].replace('-', ' '), ' for ' + f['skill'], (' in '+f['agent']) if f['agent'] else '',
        '\n'.join('- [ ] '+t for t, _ in rows), rec)
    if proposal and proposal.get('reason'): text += '\n(%s: %s)' % (proposal['class'], proposal['reason'])
    return ':::question\n%s\n:::' % text


def inbox_body(items, envs, proposals):
    lines = ['Skills drift. Nothing changes until you answer; the next check applies the answers.', '']
    asked = 0
    for f in items:
        if f['kind'] in FYI: continue
        if asked >= MAX_QUESTIONS: break
        q = question(f, envs[f['scope']], proposals.get(f['id']))
        if q: lines += [q, '']; asked += 1
    fyi = [f for f in items if f['kind'] in FYI]
    if fyi:
        lines.append('For your information (no action from the tool):')
        lines += ['- %s: `%s` %s' % (f['kind'], f['skill'], f['detail']) for f in fyi[:20]]
    left = len([f for f in items if f['kind'] not in FYI])-asked
    if left > 0: lines.append('%d more findings are in the report only.' % left)
    return '\n'.join(lines)


def _call(action, payload, cwd, runner=subprocess.run):
    env = dict(os.environ, PLANNOTATOR_CWD=str(cwd))
    done = runner(['plannotator', 'inbox', 'call', action, '-'], input=json.dumps(payload), capture_output=True,
                  text=True, cwd=str(cwd), env=env, timeout=60)
    if done.returncode: raise ValueError('Inbox %s failed: %s' % (action, (done.stderr or '').strip()[:300]))
    try: return json.loads(done.stdout)
    except ValueError: return {}


def send_inbox(items, envs, proposals, state, cwd, runner=subprocess.run):
    """Post the open findings once per change. Returns 'sent', 'unchanged' or 'nothing'."""
    open_items = list(items)
    if not open_items: state['sent'] = None; return 'nothing'
    digest = _fp(*sorted(f['id']+f['fingerprint'] for f in open_items))
    if state.get('sent') == digest: return 'unchanged'
    payload = {'subject': THREAD, 'thread': THREAD, 'body': inbox_body(open_items, envs, proposals),
               'idempotency_key': 'skills-check-'+digest}
    if state.get('message_id'): payload['supersedes'] = [state['message_id']]
    result = _call('send_message', payload, cwd, runner)
    state['sent'] = digest
    state['message_id'] = result.get('message_id') or result.get('id') or (result.get('message') or {}).get('id') or state.get('message_id')
    state['thread_id'] = result.get('thread_id') or (result.get('thread') or {}).get('thread_id') or state.get('thread_id')
    return 'sent'


def read_answers(state, cwd, runner=subprocess.run):
    """[(finding id, action, question key)] the owner answered and this tool has not handled yet."""
    thread = state.get('thread_id')
    if not thread: return []
    try: data = _call('read_thread', {'thread_id': thread, 'limit': 20}, cwd, runner)
    except ValueError: return []
    out = []
    for message in (data.get('thread') or {}).get('messages', []):
        for q in message.get('questions') or []:
            picked = ((q.get('answer') or {}).get('selected') or [])[:1]
            found = ID_MARK.search(q.get('prompt') or '')
            if picked and found and q.get('key') not in state['handled']:
                out.append((found.group(1), picked[0], q['key']))
    return out


def apply_answers(answers, items, envs, state, dry=False):
    """Run the owner's answers for findings still present; remember the rest as handled."""
    done = []; byid = {f['id']: f for f in items}
    for fid, text, key in answers:
        f = byid.get(fid)
        if f is not None:
            env = envs[f['scope']]
            act = next((a for t, a in choices_for(f, env) if t == text), None)
            if act == 'keep':
                if not dry: state['decided'][fid] = f['fingerprint']
            elif act:
                done.append((fid, act, apply_action(env, f, act) if not dry else []))
        if not dry: state['handled'].append(key)
    return done


# ---------- run ----------

def run(envs, agent=None, auto=False, inbox=False, inbox_cwd=None, base=None, ask_fn=None, runner=subprocess.run,
        plannotator_version=None, timeout=600, all_markets=False):
    """One check. Returns a result dict; raises ValueError on tool errors."""
    lockfile = Path(base or state_dir())/'skills-check.lock'; lockfile.parent.mkdir(parents=True, exist_ok=True)
    with open(lockfile, 'w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: return {'status': 'busy'}
        state = load_state(base); cwd = inbox_cwd or Path.cwd()
        by_scope = {_scope(e): e for e in envs}
        reports = {}
        scan = lambda: [f for e in envs for f in stage_a(e, plannotator_version if not e.project else None, True, all_markets, reports)]
        items = scan(); applied = []
        if inbox and state.get('thread_id'):
            applied += apply_answers(read_answers(state, cwd, runner), items, by_scope, state)
            if applied: items = scan()
        items = [f for f in items if state['decided'].get(f['id']) != f['fingerprint']]
        proposals = {}
        todo = [f for f in items if f['kind'] not in FYI]
        if agent and todo: proposals = judge(todo, agent, ask_fn, timeout, cwd)
        if auto:
            for f in list(items):
                safe = SAFE.get(f['kind'])
                if not safe: continue
                said = proposals.get(f['id'])
                if said and said['action'] != safe and said['action'] != 'ask-owner': continue
                applied.append((f['id'], safe, apply_action(by_scope[f['scope']], f, safe)))
            fixed = {a[0] for a in applied}; items = [f for f in items if f['id'] not in fixed]
        sent = send_inbox(items, by_scope, proposals, state, cwd, runner) if inbox else None
        atomic_write(state_path(base), state)
        return {'status': 'ok', 'findings': items, 'proposals': proposals, 'applied': applied, 'inbox': sent, 'reports': reports}


def print_result(result, out=print, skip=()):
    if result['status'] == 'busy': out('Another skills check is running; skipped.'); return
    for f in result['findings']:
        if f['kind'] in skip: continue
        p = result['proposals'].get(f['id']); extra = ' -> %s (%s: %s)' % (p['action'], p['class'], p['reason']) if p else ''
        out('  %-14s %-30s %-10s %s%s' % (f['kind'], f['skill'], f['agent'] or '-', f['detail'], extra))
    for fid, action, _ in result['applied']: out('  applied %s: %s' % (action, fid))
    if not [f for f in result['findings'] if f['kind'] not in skip] and not result['applied']: out('No drift found.')
    if result['inbox']: out('Inbox: %s' % result['inbox'])


# ---------- schedule ----------

LABEL = 'agent-utils.skills-check'
UNITS = {'m': 60, 'h': 3600, 'd': 86400, 'w': 604800}


def parse_every(text):
    m = re.fullmatch(r'(\d+)([mhdw])', text.strip())
    if not m or int(m.group(1)) < 1: raise ValueError('--every needs a number and m, h, d or w, e.g. 1d')
    seconds = int(m.group(1))*UNITS[m.group(2)]
    if seconds < 900: raise ValueError('--every must be at least 15m')
    return seconds


def plist_path(home=None):
    return Path(home or Path.home())/'Library/LaunchAgents'/(LABEL+'.plist')


def schedule_plist(every, agent, auto, inbox, projects, program=None, home=None):
    program = program or ([shutil.which('agents')] if shutil.which('agents') else [sys.executable, '-m', 'agent_utils.entry'])
    args = list(program)+['admin', 'skills', 'check']
    for p in projects: args += ['--also-project', str(p)]
    if agent: args += ['--agent', agent]
    if auto: args.append('--auto')
    if inbox: args.append('--inbox')
    logs = state_dir()
    return {'Label': LABEL, 'ProgramArguments': args, 'StartInterval': parse_every(every), 'RunAtLoad': False,
            'WorkingDirectory': str(Path(home or Path.home())), 'StandardOutPath': str(logs/'skills-check.log'),
            'StandardErrorPath': str(logs/'skills-check.log'), 'ProcessType': 'Background'}


def schedule_command(argv, runner=subprocess.run, home=None, out=print):
    p = argparse.ArgumentParser(prog='agents admin skills schedule', description='Run `skills check` on a schedule (launchd)')
    sub = p.add_subparsers(dest='operation', required=True)
    s = sub.add_parser('install', help='generate the launchd job (preview unless --apply)')
    s.add_argument('--every', default='1d', help='interval: 30m, 12h, 1d, 1w (default 1d)')
    s.add_argument('--agent', help='agent that judges findings: claude, codex, kiro (a fixed one; scheduled runs cannot ask)')
    s.add_argument('--auto', action='store_true'); s.add_argument('--inbox', action='store_true')
    s.add_argument('--project', action='append', default=[]); s.add_argument('--apply', action='store_true')
    s = sub.add_parser('remove', help='unload and delete the launchd job (preview unless --apply)'); s.add_argument('--apply', action='store_true')
    args = p.parse_args(argv); path = plist_path(home)
    domain = 'gui/%d' % os.getuid()
    if args.operation == 'install':
        if args.agent == 'ask': raise ValueError('A scheduled run cannot ask; name the agent')
        data = schedule_plist(args.every, args.agent, args.auto, args.inbox, args.project, home=home)
        out(plistlib.dumps(data).decode())
        if not args.apply: out('Preview only; add --apply to write %s and load it.' % path); return 0
        path.parent.mkdir(parents=True, exist_ok=True); state_dir().mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(data))
        runner(['launchctl', 'bootout', '%s/%s' % (domain, LABEL)], capture_output=True)
        done = runner(['launchctl', 'bootstrap', domain, str(path)], capture_output=True, text=True)
        if done.returncode: raise ValueError('launchctl bootstrap failed: '+(done.stderr or '').strip()[:300])
        out('Installed %s (every %s). Remove with `agents admin skills schedule remove --apply`.' % (path, args.every)); return 0
    if not path.exists(): out('No schedule installed.'); return 0
    if not args.apply: out('Would unload and delete %s; add --apply.' % path); return 0
    runner(['launchctl', 'bootout', '%s/%s' % (domain, LABEL)], capture_output=True)
    path.unlink(); out('Removed %s.' % path); return 0
