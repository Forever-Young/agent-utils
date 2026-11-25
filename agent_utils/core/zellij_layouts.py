"""Per-project Zellij tab layouts.

A layout is the *body* of one tab, in KDL. It must contain the line `{{agent}}` exactly once; it is
replaced by the agent pane (named `agent`, running the managed runner command). Other placeholders:
`{{root}}`, `{{project}}`, `{{task}}` (KDL-escaped text, for use inside quotes) and `{{logs}}` (a pane
running the project's `zellij.logs_command`, or a shell named logs). Presets ship with the package; your
own files live in ~/.config/agent-utils/zellij-layouts/NAME.kdl and win over a preset of the same name.

The tab is wrapped with the session's own `new_tab_template` (from `dump-layout`), so tab and status
bars look like the other tabs; a tab made from a bare layout string has no bars.
"""
import re

from .paths import config_dir

PRESETS = {
    'agent': '{{agent}}\n',
    'agent-shell': '''pane split_direction="vertical" {
    {{agent}}
    pane name="shell" size="30%"
}
''',
    'agent-shell-below': '''pane split_direction="horizontal" {
    {{agent}}
    pane name="shell" size="30%"
}
''',
    'agent-shell-logs': '''pane split_direction="vertical" {
    {{agent}}
    pane size="35%" split_direction="horizontal" {
        pane name="shell"
        {{logs}}
    }
}
''',
}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
FALLBACK_TEMPLATE = '''default_tab_template {
    pane size=1 borderless=true {
        plugin location="zellij:tab-bar"
    }
    children
    pane size=1 borderless=true {
        plugin location="zellij:status-bar"
    }
}'''


def layouts_dir():
    return config_dir()/'zellij-layouts'


def check_name(name):
    if not isinstance(name, str) or not NAME.match(name):
        raise ValueError('Layout names use letters, digits, dot, dash and underscore: %r' % (name,))
    return name


def available():
    """[(name, source)] with source 'preset', 'file' or 'file (overrides preset)'."""
    names = {n: 'preset' for n in PRESETS}
    if layouts_dir().is_dir():
        for path in sorted(layouts_dir().glob('*.kdl')):
            if NAME.match(path.stem):
                names[path.stem] = 'file (overrides preset)' if path.stem in PRESETS else 'file'
    return sorted(names.items())


def source(name):
    check_name(name)
    path = layouts_dir()/(name + '.kdl')
    if path.is_file():return path.read_text()
    if name in PRESETS:return PRESETS[name]
    raise ValueError('Unknown layout %r; see `agents zellij layout list`' % name)


def esc(text):
    return str(text).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')


def q(text):
    return '"' + esc(text) + '"'


def command_pane(name, argv, focus=False, suspended=False):
    """One KDL pane node running argv (one node per line; one-line `{ args ...; }` does not parse)."""
    if not argv:raise ValueError('empty command')
    head = 'pane name=%s%s command=%s%s' % (q(name), ' focus=true' if focus else '', q(argv[0]),
                                           ' start_suspended=true' if suspended else '')
    if len(argv) == 1:return head
    return head + ' {\n    args ' + ' '.join(q(a) for a in argv[1:]) + '\n}'


def indent(text, prefix):
    return '\n'.join(prefix + line if line.strip() else line for line in text.splitlines())


def check_body(body):
    count = len(re.findall(r'(?m)^\s*\{\{agent\}\}\s*$', body))
    if count != 1:
        raise ValueError('A layout needs the line {{agent}} exactly once (found %d)' % count)
    stripped = re.sub(r'"(?:\\.|[^"\\])*"', '', body)
    if stripped.count('{') != stripped.count('}'):
        raise ValueError('Unbalanced braces in the layout')


def tab_template(dump):
    """The session's new_tab_template as a default_tab_template, else a tab-bar/status-bar fallback."""
    i = (dump or '').find('new_tab_template {')
    if i < 0:return FALLBACK_TEMPLATE
    depth, j = 0, i
    while j < len(dump):
        if dump[j] == '{':depth += 1
        elif dump[j] == '}':
            depth -= 1
            if depth == 0:break
        j += 1
    block = dump[i:j + 1]
    if not re.search(r'(?m)^\s*pane\s*$', block):return FALLBACK_TEMPLATE
    block = re.sub(r'(?m)^(\s*)pane\s*$', r'\1children', block, count=1)
    return block.replace('new_tab_template', 'default_tab_template', 1)


def render(name, tab_name, root, agent_argv, project=None, task=None, template=None):
    """The KDL for `new-tab --layout-string`."""
    body = source(name)
    check_body(body)
    project = project or {}
    logs = (project.get('zellij') or {}).get('logs_command')
    logs_pane = command_pane('logs', logs, suspended=True) if logs else 'pane name="logs"'
    for key, value in (('root', root), ('project', project.get('name', '')), ('task', task or '')):
        body = body.replace('{{%s}}' % key, esc(value))

    def block(match):
        return indent(match.group(2), match.group(1))
    body = re.sub(r'(?m)^(\s*)\{\{logs\}\}\s*$', lambda m: indent(logs_pane, m.group(1)), body)
    body = re.sub(r'(?m)^(\s*)\{\{agent\}\}\s*$', lambda m: indent(command_pane('agent', agent_argv, focus=True), m.group(1)), body)
    return 'layout {\n%s\n    tab name=%s cwd=%s focus=true {\n%s\n    }\n}\n' % (
        indent(template or FALLBACK_TEMPLATE, '    '), q(tab_name), q(root), indent(body.rstrip('\n'), '        '))


def new_file(name, preset='agent-shell'):
    check_name(name)
    if preset not in PRESETS:raise ValueError('Unknown preset %r' % preset)
    path = layouts_dir()/(name + '.kdl')
    if path.exists():raise ValueError('%s exists; edit it instead' % path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('// Tab body for agents zellij. Keep the {{agent}} line exactly once.\n' + PRESETS[preset])
    return path
