"""The entries of `agents configure` and the steps behind each one.

Every entry is a *flow*: a generator that yields what it needs from the person and receives the
answer. Steps:
  ('choice', title, [(label, value)], body?)   pick one, with an optional text shown above; None cancels
  ('text', title, initial)              type a value; None or empty cancels
  ('confirm', title, body)              yes or no; body is the diff, or None for the output of the last 'run'
  ('run', action, arg, apply)           run an Action (preview or apply); the answer is the exit code
  ('say', text)                         show a line
The list menu (`drive_sync`) and the Textual app drive the same flows, so both do exactly the
same changes: settings and project edits go through `settings.edit` (diff, lock, backup, undo),
everything else runs the existing commands as child processes.
"""
from pathlib import Path
import shlex

from . import settings
from .agents import AGENTS
from .setup import optional

# Actions reached through their own entries rather than listed one by one.
HIDDEN_ACTIONS = {'optional', 'optional-list', 'zellij-mobile-install', 'zellij-mobile-remove',
                  'schedule-install', 'schedule-remove'}
HOOK_REMOVE = {'kiro-hooks': 'remove-kiro-hooks', 'inbox-relay': 'remove-inbox-relay',
               'session-names': 'remove-session-names', 'zellij-mobile': 'zellij-mobile-remove',
               'zellij-status': 'remove-zellij-status', 'zellij-adopt': 'remove-zellij-status'}


def _lines():
    lines = []
    return lines, lines.append


def _confirm_body(lines):
    return '\n'.join(lines[:-1] if lines and lines[-1].startswith('Preview only') else lines)


# ---------------------------------------------------------------- settings

def setting_flow(row):
    def flow():
        key = row['key']
        if key.endswith('.*'):
            name = yield ('text', 'Name of the variable' if 'variables' in key else 'Name (%s)' % ('agent' if 'launch_args' in key else 'member'), '')
            if not name or not name.strip():
                return
            key = key[:-1] + name.strip()
        setting = settings.find(key)
        remove = False
        if setting.kind == 'bool':
            text = 'off' if row['value'] else 'on'
        elif setting.kind == 'choice':
            text = yield ('choice', key, [(c, c) for c in setting.choices])
            if text is None:
                return
        else:
            text = yield ('text', '%s: %s\n(current: %s; type - to remove it and return to the default)' % (key, row['help'], row['shown']),
                          '' if row['value'] in (None, '') else str(row['shown']))
            if text is None or not text.strip():
                return
            remove = text.strip() == '-'
        value = None if remove else text
        lines, out = _lines()
        if not settings.change(key, value, remove=remove, apply=False, out=out):
            yield ('say', '%s: no change' % key)
            return
        if (yield ('confirm', 'Write %s?' % key, _confirm_body(lines))):
            lines, out = _lines()
            settings.change(key, value, remove=remove, apply=True, out=out)
            yield ('say', lines[-1])
        else:
            yield ('say', 'Not written.')
    return flow


# ---------------------------------------------------------------- actions

def action_flow(a, arg=None):
    def flow():
        x = arg
        if a.takes and not x:
            x = yield ('text', '%s needs %s' % (a.id, a.takes), '')
            if not x or not x.strip():
                return
            x = x.strip()
        if a.direct:
            if (yield ('confirm', 'Run %s now?' % a.id, a.help)):
                yield ('run', a, x, True)
            return
        yield ('run', a, x, False)
        if a.apply and (yield ('confirm', 'Apply %s?' % a.id, None)):
            yield ('run', a, x, True)
    return flow


# ---------------------------------------------------------------- hooks and schedules

def hook_flow(name, text, configured):
    def flow():
        if configured is True:
            choice = yield ('choice', '%s is configured' % text, [('Remove it', 'remove'), ('Apply again', 'apply')])
        else:
            choice = yield ('choice', text, [('Preview and apply', 'apply'), ('Remove it', 'remove')])
        if choice == 'apply':
            a = settings.action('optional')
            yield ('run', a, name, False)
            if (yield ('confirm', 'Apply %s?' % name, None)):
                yield ('run', a, name, True)
        elif choice == 'remove':
            remove_id = HOOK_REMOVE.get(name)
            if not remove_id:
                yield ('say', 'There is no remove command for %s; delete the block it added to ~/.zshrc (marked BEGIN agterm-utils zsh startup).' % name)
                return
            a = settings.action(remove_id)
            yield ('run', a, None, False)
            if (yield ('confirm', 'Remove %s?' % name, None)):
                yield ('run', a, None, True)
    return flow


def schedule_flow(installed):
    def flow():
        a = settings.action('schedule-remove' if installed else 'schedule-install')
        yield from action_flow(a)()
    return flow


# ---------------------------------------------------------------- projects and windows

def _find(data, kind, ident):
    if kind == 'window':
        found = next((w for w in data['windows'] if w['name'] == ident), None)
    else:
        found = next((p for w in data['windows'] for p in w.get('projects', []) if p['id'] == ident), None)
    if found is None:
        raise ValueError('%s %r not found' % (kind, ident))
    return found


def _window_of(data, pid):
    return next(w for w in data['windows'] for p in w.get('projects', []) if p['id'] == pid)


def project_entries():
    try:
        data = settings.read('config')
    except ValueError:
        return []
    rows = []
    for w in data.get('windows', []):
        rows.append(('Window %s: default %s, %d projects' % (w['name'], w.get('default_agent', '-'), len(w.get('projects', []))),
                     ('flow', target_flow('window', w['name']))))
        for p in w.get('projects', []):
            rows.append(('  %s (%s): %s, default %s' % (p.get('name', p['id']), p['id'],
                                                         'enabled' if p.get('enabled', True) else 'disabled',
                                                         p.get('default_agent') or w.get('default_agent', '-')),
                         ('flow', target_flow('project', p['id']))))
    rows.append(('Add a project', ('flow', add_project_flow)))
    return rows


def target_flow(kind, ident):
    def flow():
        data = settings.read('config')
        target = _find(data, kind, ident)
        ops = [('Set the default agent', 'default_agent'), ('Set the agent order', 'agent_order'),
               ('Set the Zellij layout', 'zellij_layout'), ('Set the Zellij session', 'zellij_session')]
        if kind == 'project':
            ops = [('Disable' if target.get('enabled', True) else 'Enable', 'toggle'), ('Rename', 'name'),
                   ('Change the root folder', 'root'), ('Move to another window', 'window')] + ops
        op = yield ('choice', '%s %s' % (kind.capitalize(), ident), ops)
        if op is None:
            return
        value = None
        if op == 'default_agent':
            value = yield ('choice', 'Default agent', [(a, a) for a in AGENTS])
        elif op == 'window':
            value = yield ('choice', 'Move to window', [(w['name'], w['name']) for w in data['windows'] if w['name'] != _window_of(data, ident)['name']])
        elif op != 'toggle':
            hint = {'agent_order': 'Agent order, comma separated, * = the rest (- clears)',
                    'zellij_layout': 'Zellij layout: a preset or your own file (- clears)',
                    'zellij_session': 'Zellij session: shared, project or a name (- clears)',
                    'name': 'New name', 'root': 'New root folder'}[op]
            current = {'agent_order': ','.join(target.get('agent_order', [])), 'name': target.get('name', ''),
                       'root': ', '.join(target.get('roots', [])),
                       'zellij_layout': (target.get('zellij') or {}).get('layout', ''),
                       'zellij_session': (target.get('zellij') or {}).get('session', '')}[op]
            value = yield ('text', hint, current)
        if op != 'toggle' and (value is None or (isinstance(value, str) and not value.strip())):
            return
        label = '%s %s' % (kind, ident)

        def mutate(d):
            t = _find(d, kind, ident)
            clear = isinstance(value, str) and value.strip() == '-'
            if op == 'toggle':
                t['enabled'] = not t.get('enabled', True)
            elif op == 'default_agent':
                t['default_agent'] = value
            elif op == 'agent_order':
                if clear:
                    t.pop('agent_order', None)
                else:
                    order = [x.strip() for x in value.split(',') if x.strip()]
                    from ..cli import check_agent_order
                    check_agent_order(order)
                    t['agent_order'] = order
            elif op in ('zellij_layout', 'zellij_session'):
                from ..agterm.configuration import set_zellij
                set_zellij(d, ident if kind == 'window' else None, ident if kind == 'project' else None,
                           {op.split('_')[1]: '' if clear else value})
            elif op == 'name':
                t['name'] = value.strip()
            elif op == 'root':
                t['roots'] = [str(Path(r.strip()).expanduser().resolve()) for r in value.split(',') if r.strip()]
            elif op == 'window':
                src = _window_of(d, ident)
                src['projects'].remove(t)
                next(w for w in d['windows'] if w['name'] == value).setdefault('projects', []).append(t)
        yield from _commit_flow(label, mutate)
    return flow


def _commit_flow(label, mutate):
    lines, out = _lines()
    if not settings.edit('config', mutate, label, False, out):
        yield ('say', '%s: no change' % label)
        return
    if (yield ('confirm', 'Write the change to %s?' % label, _confirm_body(lines))):
        lines, out = _lines()
        settings.edit('config', mutate, label, True, out)
        yield ('say', lines[-1])
    else:
        yield ('say', 'Not written.')


def add_project_flow():
    data = settings.read('config')
    windows = [w['name'] for w in data.get('windows', [])]
    pid = yield ('text', 'Project id (a short unique word)', '')
    if not pid or not pid.strip():
        return
    pid = pid.strip()
    window = yield ('choice', 'Window', [(w, w) for w in windows] + [('A new window...', '')])
    if window is None:
        return
    if window == '':
        window = yield ('text', 'Name of the new window', '')
        if not window or not window.strip():
            return
        window = window.strip()
    root = yield ('text', 'Root folder of the project', '')
    if not root or not root.strip():
        return
    agent = yield ('choice', 'Default agent', [(a, a) for a in AGENTS])
    if agent is None:
        return

    def mutate(d):
        if any(p['id'] == pid for w in d['windows'] for p in w.get('projects', [])):
            raise ValueError('Project ID already exists')
        w = next((x for x in d['windows'] if x['name'] == window), None)
        if w is None:
            w = {'name': window, 'default_agent': agent, 'projects': []}
            d['windows'].append(w)
        w.setdefault('projects', []).append({'id': pid, 'name': pid, 'roots': [str(Path(root.strip()).expanduser().resolve())],
                                             'default_agent': agent})
    yield from _commit_flow('project %s' % pid, mutate)


# ---------------------------------------------------------------- skills

def _cmd(id, section, help, argv, apply):
    return settings.Action(id, section, help, argv, apply)


def _preview_apply(a, what):
    """Preview, ask, apply: the steps every command-backed change shares. Returns via 'applied' in the last answer."""
    yield ('run', a, None, False)
    if (yield ('confirm', what, None)):
        yield ('run', a, None, True)
        return True
    yield ('say', 'Not applied.')
    return False


def skill_flow(row):
    name = row['name']

    def flow():
        ops = []
        for agent, state in row['agents'].items():
            if state == 'excluded':
                ops.append(('Include for %s (now excluded)' % agent, ('include', agent)))
            elif state in ('ok', 'missing', 'broken'):
                ops.append(('Exclude for %s (now %s)' % (agent, state), ('exclude', agent)))
        ops += [('Mark as reviewed', ('reviewed', None)), ('Remove from the store and the agents', ('remove', None))]
        op = yield ('choice', 'Skill %s' % name, ops)
        if op is None:
            return
        kind, agent = op
        if kind in ('include', 'exclude'):
            a = _cmd('skill-policy', 'skills', '%s %s for %s' % (kind, name, agent),
                     ['admin', 'skills', kind, name, '--agent', agent], '--apply')
            if (yield from _preview_apply(a, '%s %s for %s?' % (kind.capitalize(), name, agent))):
                yield ('run', _cmd('skills-link', 'skills', 'Create or remove links per policy', ['admin', 'skills', 'link'], '--apply'), None, True)
        elif kind == 'reviewed':
            yield from _preview_apply(_cmd('skill-reviewed', 'skills', 'reviewed', ['admin', 'skills', 'reviewed', name], '--apply'), 'Record %s as reviewed?' % name)
        else:
            yield from _preview_apply(_cmd('skill-remove', 'skills', 'remove', ['admin', 'skills', 'remove', name], '--apply'), 'Remove %s?' % name)
    return flow


def add_skill_flow():
    src = yield ('text', 'Skill to add: a folder, or a git URL#subfolder', '')
    if not src or not src.strip():
        return
    yield from _preview_apply(_cmd('skill-add', 'skills', 'add', ['admin', 'skills', 'add', src.strip()], '--apply'), 'Add %s to the store?' % src.strip())


def agent_check_flow():
    from .ask import ADAPTERS
    agent = yield ('choice', 'Which agent reviews the findings? (read-only; takes a few minutes)', [(a, a) for a in ADAPTERS])
    if agent is None:
        return
    yield ('run', _cmd('skills-check-agent', 'skills', 'Drift check reviewed by %s' % agent,
                       ['admin', 'skills', 'check', '--agent', agent], None), None, False)


def audit_flow():
    from .ask import ADAPTERS
    path = yield ('text', 'Project folder whose AGENTS.md and agent rule files to audit', '')
    if not path or not path.strip():
        return
    agent = yield ('choice', 'Also ask an agent for proposals? (read-only; takes a few minutes)',
                   [('No, the basic audit only', '')] + [(a, a) for a in ADAPTERS])
    if agent is None:
        return
    argv = ['admin', 'skills', 'audit', path.strip()] + (['--agent', agent] if agent else [])
    yield ('run', _cmd('skills-audit', 'skills', 'Project instruction audit', argv, None), None, False)


def skill_entries():
    from . import skills
    rows = skills.status(skills.Env(project=None))
    own = [r for r in rows if r['store'] == 'yes' and 'owner' not in r
           and not {'managed', 'external'} & set(r['agents'].values())]
    out = []
    for r in own:
        shown = ', '.join('%s %s' % (a, st) for a, st in r['agents'].items() if st not in ('-', None))
        out.append(('%s: %s' % (r['name'], shown or 'not linked'), ('flow', skill_flow(r))))
    others = len(rows) - len(own)
    if others:
        out.append(('%d more skills come from plugins and agents (not in your store); see Run: skills status' % others,
                    ('info', 'Skills from plugins or agents are managed by them. `agents admin skills status` lists them.')))
    out.append(('Add a skill to the store', ('flow', add_skill_flow)))
    out.append(('Drift check with an agent review (read-only)', ('flow', agent_check_flow)))
    out.append(("Audit a project's instruction files", ('flow', audit_flow)))
    return out


# ---------------------------------------------------------------- shared rules (snippets)

def rule_flow(sid, title, state, variable=None, body=''):
    def flow():
        if state == 'unset':
            yield ('say', '%s needs the variable {{%s}}: set it in Personalization first.' % (title, variable))
            return
        ops = []
        if state == 'missing':
            ops.append(('Turn on', ['--add', sid]))
        else:
            ops.append(('Turn off', ['--remove', sid] + (['--force'] if state == 'edited' else [])))
            if state == 'stale':
                ops.append(('Update to the current text', ['--update']))
            if state == 'edited':
                ops.append(('Replace my edited copy with the current text', ['--add', sid, '--force']))
        op = yield ('choice', '%s (%s)' % (title, state), ops, body)
        if op is None:
            return
        a = _cmd('rule-change', 'instructions', title, ['admin', 'instructions', 'compose'] + op, '--yes')
        yield from _preview_apply(a, 'Apply to common.md and every agent?')
    return flow


def rule_entries():
    from . import snippets
    try:
        lib = snippets.library()
        path = snippets.target_path()
        text = path.read_text() if path.is_file() else ''
        states = {r['id']: r for r in snippets.inspect(text, lib, snippets.variables())}
    except (OSError, ValueError) as exc:
        return [('Cannot read the rules: %s' % exc, ('info', str(exc)))]
    values = snippets.variables()
    out = []
    for sid, snip in lib.items():
        r = states.get(sid, {'state': 'missing'})
        on = r['state'] in ('ok', 'stale', 'edited')
        custom = not Path(snip['path']).resolve().is_relative_to(snippets.LIBRARY.resolve())
        note = '' if r['state'] in ('ok', 'missing') else ' [%s]' % ('needs {{%s}}' % r['variable'] if r['state'] == 'unset' else r['state'])
        try:
            text = snippets.render(snip, values)
        except KeyError:
            text = snip['body']
        body = '%s\n%s rule, file %s\napplies to: %s\n\n%s' % (
            snip['summary'], 'Custom' if custom else 'Bundled', snip['path'], ', '.join(snip['applies_to']), text)
        out.append(('[%s] %s%s: %s%s' % ('on' if on else 'off', '(custom) ' if custom else '', snip['title'], snip['summary'], note),
                    ('flow', rule_flow(sid, snip['title'], r['state'], r.get('variable'), body))))
    out.append(('Add a custom rule (a file in %s)' % snippets.user_dir(), ('flow', add_rule_flow)))
    return out


def add_rule_flow():
    import re
    from . import snippets
    sid = yield ('text', 'Id of the rule (letters, digits, - and _), also the file name', '')
    if not sid or not re.fullmatch(r'[\w-]{1,48}', sid.strip()):
        if sid:
            yield ('say', 'The id may only have letters, digits, - and _.')
        return
    sid = sid.strip()
    title = yield ('text', 'Title', '')
    if not title or not title.strip():
        return
    summary = yield ('text', 'One-line description', '')
    if summary is None:
        return
    text = yield ('text', 'The rule text (one line here; edit the file for more)', '')
    if not text or not text.strip():
        return
    path = snippets.user_dir()/(sid+'.md')
    if path.exists():
        yield ('say', '%s already exists; edit that file instead.' % path)
        return
    content = '---\nid: %s\ntitle: %s\nsummary: %s\napplies-to: all\ndefault: off\nconflicts:\n---\n%s\n' % (
        sid, title.strip(), summary.strip(), text.strip())
    if (yield ('confirm', 'Create %s?' % path, content)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        yield ('say', 'Created %s. It is off; pick it in the list to turn it on.' % path)
    else:
        yield ('say', 'Not created.')


# ---------------------------------------------------------------- personalization

def _variable_rows(values):
    """Rows for the variables the rules use (with their meaning), the ones set for other reasons, and an add row."""
    from . import snippets
    try:
        used = snippets.used_variables(snippets.library())
    except (OSError, ValueError):
        used = {}
    current = {r['key'].rsplit('.', 1)[-1]: r for r in values if not r['key'].endswith('.*')}
    rows = []
    for name, info in used.items():
        have = current.get(name)
        value = have['value'] if have else ''
        meaning = info['meaning'] or 'used in the text of these rules'
        rows.append({'key': 'snippets.variables.' + name, 'section': 'snippets', 'value': value,
                     'shown': value or '(not set: rules that use it stay off)', 'default': '',
                     'set': bool(have), 'file': 'snippets',
                     'help': '{{%s}}, %s [rules: %s]. Value' % (name, meaning, ', '.join(info['rules']))})
    for name, have in current.items():
        if name not in used:
            rows.append(dict(have, help='{{%s}} (your own variable, for custom rules). Value' % name))
    fam = next(s for s in settings.SETTINGS if s.key == 'snippets.variables.*')
    rows.append({'key': fam.key, 'section': 'snippets', 'value': None, 'shown': '', 'default': '',
                 'set': False, 'help': 'Add your own variable (name and value) for custom rules', 'file': fam.file})
    return rows


# ---------------------------------------------------------------- entries

def entries(section):
    """[(label, entry)] for one section; entry is ('flow', factory) or ('info', text)."""
    rows = []
    if section == 'projects':
        rows += project_entries()
    else:
        try:
            values = settings.values(section)
        except ValueError as exc:
            return [('Cannot read settings: %s' % exc, ('info', str(exc)))]
        if section == 'snippets':
            values = _variable_rows(values)
        for r in values:
            if r['key'].endswith('.*'):
                label = r['help'] if r['help'].startswith('Add your own') else 'Add: %s' % r['help']
            else:
                label = '%s: %s' % (r['help'], r['shown'])
            rows.append((label, ('flow', setting_flow(r))))
    if section == 'skills':
        rows += skill_entries()
    if section == 'instructions':
        rows += rule_entries()
    if section == 'schedules':
        installed = (Path.home()/'Library/LaunchAgents/agent-utils.skills-check.plist').exists()
        rows.append(('Daily skills drift check: %s' % ('installed (select to remove)' if installed else 'not installed (select to install)'),
                     ('flow', schedule_flow(installed))))
    if section == 'hooks':
        rc = Path.home()/'.zshrc'
        zsh_on = rc.is_file() and '# BEGIN agterm-utils zsh startup' in rc.read_text(errors='replace')
        for name, _module, text, available, done, why in optional.items(Path.home()):
            if name == 'zsh-startup':  # not auto-applied by `optional --detected`, so its state is read here
                done = zsh_on
                text = 'One-shot agterm startup hook in zsh (session chooser in new agterm shells)'
            state = 'configured' if done else 'not configured' if done is False else 'not detected'
            rows.append(('%s: %s%s' % (text, state, '' if available else ' (unavailable: %s)' % why),
                         ('flow', hook_flow(name, text, done))))
    for a in settings.ACTIONS:
        if a.section == section and a.id not in HIDDEN_ACTIONS and not a.id.startswith('remove-'):
            rows.append(('Run: %s' % a.help, ('flow', action_flow(a))))
    return rows


# ---------------------------------------------------------------- list-menu driver

def drive_sync(entry, pick, ask, runner, out):
    kind, item = entry
    if kind == 'info':
        out(item)
        ask('Enter to continue ')
        return
    gen = item()
    try:
        step = next(gen)
        while True:
            t = step[0]
            if t == 'choice':
                if len(step) > 3 and step[3]:
                    out(step[3])
                answer = pick(step[1], step[2])
                if answer is None:
                    gen.close()
                    return
            elif t == 'text':
                answer = ask('%s%s: ' % (step[1], ' [%s]' % step[2] if step[2] else '')).strip()
                if not answer and step[2]:
                    answer = None
                if not answer:
                    gen.close()
                    return
            elif t == 'confirm':
                if step[2]:  # None: the preview of the last run is already on screen
                    out(step[2])
                answer = ask('%s [y/N] ' % step[1]).strip().lower() == 'y'
            elif t == 'run':
                answer = settings.run(step[1], step[2], step[3], runner, out)
            else:
                out(step[1])
                answer = None
            step = gen.send(answer)
    except StopIteration:
        pass
    ask('Enter to continue ')
