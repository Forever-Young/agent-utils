"""Agent notifications: one tool that agents and hooks call to send an event.

`agents agent notify send --agent NAME --session NAME --event done|input|failed [--detail TEXT]`
renders a title and body from templates and posts them. The only transport is ntfy
(JSON publish to a configurable URL). Disabled by default; a disabled or broken setup
never fails the calling hook.

`agents admin notify show|enable|disable|url URL|test` manages the external config
`<config dir>/notify.json`.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

from .files import atomic_write
from .paths import config_dir

EVENTS = {'done': ('Done', 3), 'input': ('Input needed', 4), 'failed': ('Failed', 4), 'info': ('Info', 3)}
DEFAULTS = {'version': 1, 'enabled': False, 'transport': 'ntfy', 'url': '',
            'title': '{session}', 'body': '{event_text}{detail_suffix}', 'mobile_host': ''}


def config_file():
    return config_dir() / 'notify.json'


def load():
    data = dict(DEFAULTS)
    try:
        stored = json.loads(config_file().read_text())
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    return data


def save(data):
    atomic_write(config_file(), {k: data[k] for k in DEFAULTS})


def valid_url(url):
    from urllib.parse import urlparse
    parts = urlparse(url)
    return parts.scheme in ('http', 'https') and bool(parts.netloc) and len(parts.path.strip('/')) > 0


def render(config, agent, session, event, detail=''):
    """Pure: returns (title, body, priority). Unknown placeholders are left as written."""
    if event not in EVENTS:
        raise ValueError('event must be one of: ' + ', '.join(EVENTS))
    text, priority = EVENTS[event]
    values = _Values(agent=agent or 'agent', session=session or '', event=event, event_text=text,
                     detail=detail or '', detail_suffix=(': ' + detail) if detail else '')
    title = config['title'].format_map(values).strip(' :') or (session or agent or 'agent')
    return title, config['body'].format_map(values).strip(), priority


class _Values(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def payload(url, title, body, priority, event):
    from urllib.parse import urlparse
    parts = urlparse(url)
    base = '%s://%s' % (parts.scheme, parts.netloc)
    return base, {'topic': parts.path.strip('/'), 'title': title, 'message': body, 'priority': priority,
                  'tags': [event]}


def post(url, title, body, priority, event, timeout=5, click=None):
    base, data = payload(url, title, body, priority, event)
    if click:
        data['click'] = click
    request = urllib.request.Request(base, data=json.dumps(data).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status


def find_session_name(sid, tree):
    stack = [tree]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get('id') == sid and isinstance(item.get('name'), str):
                return item['name']
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return None


def default_session(env=None):
    """Explicit env, then the agterm sidebar name of this session, then the folder name."""
    env = os.environ if env is None else env
    if env.get('AGENT_UTILS_SESSION_NAME'):
        return env['AGENT_UTILS_SESSION_NAME']
    sid, ctl = env.get('AGTERM_SESSION_ID'), env.get('AGTERMCTL') or shutil.which('agtermctl')
    if sid and ctl:
        argv = [ctl, 'tree', '--json'] + (['--socket', env['AGTERM_SOCKET']] if env.get('AGTERM_SOCKET') else [])
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=3, stdin=subprocess.DEVNULL).stdout
            tree = json.loads(out)
            name = find_session_name(sid, tree)
            if name:
                return name
        except Exception:
            pass
    return Path.cwd().name


def parse_hook(agent, text):
    """Map an agent's hook JSON to (event, detail), or None when the payload is not a notify event."""
    try:
        data = json.loads(text or '{}')
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if agent == 'claude':
        kind = data.get('hook_event_name')
        if kind == 'Stop':
            return 'done', ''
        if kind == 'Notification':
            message = data.get('message')
            return 'input', message[:200] if isinstance(message, str) else ''
    if agent == 'codex' and data.get('type') == 'agent-turn-complete':
        return 'done', ''
    return None


def send(agent, session, event, detail='', dry_run=False, config=None):
    """Returns a short status string. Never raises for transport problems."""
    config = config or load()
    if not config['enabled']:
        return 'disabled'
    if not valid_url(config['url']):
        return 'no valid url configured'
    title, body, priority = render(config, agent, session, event, detail)
    if dry_run:
        return 'would send to %s: %s | %s | priority %s' % (config['url'], title, body, priority)
    try:
        from urllib.parse import quote
        host, sid = config.get('mobile_host'), os.environ.get('AGTERM_SESSION_ID')
        click = ('agterm-mobile://%s/session/%s' % (quote(host, safe=''), quote(sid, safe=''))) if host and sid else None
        post(config['url'], title, body, priority, event, click=click)
    except Exception as error:
        return 'send failed: %s' % error
    return 'sent'


def command(argv, prog='agents admin notify'):
    parser = argparse.ArgumentParser(prog=prog, description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true')
    sub = parser.add_subparsers(dest='action', required=True)
    s = sub.add_parser('send', help='send one event (called by agents and hooks; never fails the caller)')
    s.add_argument('--agent', required=True)
    s.add_argument('--session', help='default: AGENT_UTILS_SESSION_NAME, the agterm session name, the folder name')
    s.add_argument('--event', choices=list(EVENTS), required=True)
    s.add_argument('--detail', default='', help='what finished or what is needed, besides the session name')
    s.add_argument('--quiet', action='store_true', help='print nothing')
    h = sub.add_parser('hook', help="adapter for an agent's own hook payload (stdin or argument); maps it to an event")
    h.add_argument('agent', choices=['claude', 'codex'])
    h.add_argument('payload', nargs='?', help='codex passes its JSON as an argument; claude uses stdin')
    for name, help_text in (('show', 'print the configuration'), ('enable', 'turn notifications on'),
                            ('disable', 'turn notifications off'), ('test', 'send a test event now')):
        sub.add_parser(name, help=help_text)
    m = sub.add_parser('mobile-host', help='set the saved mobile app Mac hostname for notification links; empty disables')
    m.add_argument('host')
    u = sub.add_parser('url', help='set the ntfy URL (https://host/topic)')
    u.add_argument('value')
    t = sub.add_parser('template', help='set the title and/or body template')
    t.add_argument('--title');t.add_argument('--body')
    args = parser.parse_args(argv)
    if args.action == 'send':
        result = send(args.agent, args.session or default_session(), args.event, args.detail, args.dry_run)
        if not args.quiet and result not in ('disabled', 'sent'):
            print(result)
        return 0
    if args.action == 'hook':
        event = parse_hook(args.agent, args.payload if args.payload is not None else sys.stdin.read())
        if event:
            result = send(args.agent, default_session(), event[0], event[1], args.dry_run)
            if result not in ('disabled', 'sent'):
                print(result)
        return 0
    config = load()
    if args.action == 'show':
        print(json.dumps(config, indent=2))
        print('file: %s%s' % (config_file(), '' if config_file().exists() else ' (not written yet)'))
        print('placeholders: {agent} {session} {event} {event_text} {detail} {detail_suffix}')
        return 0
    if args.action == 'test':
        result = send('agent-utils', 'test', 'info', 'test message', args.dry_run, dict(config, enabled=True))
        print(result)
        return 0 if result in ('sent',) or result.startswith('would send') else 1
    if args.action == 'enable' and not valid_url(config['url']):
        raise ValueError('set the URL first: agents admin notify url https://ntfy.sh/TOPIC')
    if args.action == 'url' and not valid_url(args.value):
        raise ValueError('URL must be http(s)://host/topic')
    if args.action in ('enable', 'disable'):
        config['enabled'] = args.action == 'enable'
    elif args.action == 'mobile-host':
        if any(c in args.host for c in ('/', '?', '#', ' ', '\n')):
            raise ValueError('mobile-host must be a hostname or saved Mac UUID')
        config['mobile_host'] = args.host
    elif args.action == 'url':
        config['url'] = args.value
    elif args.action == 'template':
        if args.title is None and args.body is None:
            raise ValueError('template needs --title and/or --body')
        for key in ('title', 'body'):
            if getattr(args, key) is not None:
                config[key] = getattr(args, key)
        render(config, 'a', 's', 'done')
    if args.dry_run:
        print('Would write %s:\n%s' % (config_file(), json.dumps(config, indent=2)))
        return 0
    save(config)
    print('Saved %s (enabled: %s)' % (config_file(), config['enabled']))
    return 0


if __name__ == '__main__':
    sys.exit(command(sys.argv[1:]))
