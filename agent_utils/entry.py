"""The single `agents` entry point.

Commands are grouped by where they run:

  agterm   inside an agterm session: launch, sessions, restore, startup menu
  zellij   inside a Zellij pane: open, find and focus project agent sessions as tabs
  remote   from an SSH shell or a phone: SSH shells, attach/detach agterm sessions
  agent    called by agents and hooks: session names, read-only consults, notifications
  mobile   run by the agterm mobile app over SSH: JSON-lines control, pane attach
  admin    configuration and upkeep: config (all settings), projects, skills, instructions, Inbox, notify
  configure  the interactive menu of `agents admin config` (same as `agents admin config tui`)

A bare `agents` opens the menu that fits the detected context. `--context`
overrides detection. There are no old-name aliases; callers use these names.
"""
import os
import sys

AGTERM_COMMANDS = ('list', 'menu', 'new', 'ensure', 'resume', 'coordinator', 'bootstrap', 'doctor', 'worktree',
                   'validate', 'startup', 'restore', 'session', 'task', 'dashboard', 'history', 'gui', 'cheatsheet')
ADMIN_PROJECT_COMMANDS = ('project', 'settings', 'inbox')
ADMIN_COMMANDS = ADMIN_PROJECT_COMMANDS + ('config', 'skills', 'instructions', 'notify')
AGENT_COMMANDS = ('rename', 'sync', 'ask', 'notify')
REMOTE_ACTIONS = ('menu', 'list', 'attach', 'terminal', 'shell', 'detach', 'lead')
MOBILE_COMMANDS = ('serve', 'attach')
ZELLIJ_COMMANDS = ('run', 'launcher', 'menu', 'open', 'list', 'focus', 'close', 'adopt', 'layout', 'doctor')

GROUPS = {
    'agterm': ('inside an agterm session', AGTERM_COMMANDS),
    'remote': ('from an SSH shell or phone', REMOTE_ACTIONS),
    'agent': ('called by agents and hooks', AGENT_COMMANDS),
    'admin': ('configuration and upkeep', ADMIN_COMMANDS),
    'zellij': ('project agent sessions as Zellij tabs', ZELLIJ_COMMANDS),
    'mobile': ('run by the mobile app over SSH', MOBILE_COMMANDS),
    'configure': ('interactive settings and actions (= admin config tui)', ()),
}
# Groups first in help for each context. Every group stays callable in every context.
RELEVANT = {'zellij': ('zellij', 'agent', 'admin', 'configure', 'agterm', 'remote', 'mobile'),
            'agterm': ('agterm', 'agent', 'admin', 'configure', 'remote', 'mobile'),
            'remote': ('remote', 'admin', 'configure', 'agent', 'agterm', 'mobile'),
            'plain': ('admin', 'configure', 'agterm', 'remote', 'agent', 'mobile')}


def agterm_alive(env):
    """True when agtermctl confirms this agterm session still exists. Any failure counts as not alive."""
    try:
        from .cli import Agterm
        window = env.get('AGTERM_WINDOW_ID')
        tree = Agterm().call('tree', *(['--window', window] if window else []), json_result=True)
        tree = tree.get('tree', tree)
        return any(s.get('id') == env.get('AGTERM_SESSION_ID')
                   for w in tree.get('workspaces', []) for s in w.get('sessions', []))
    except Exception:
        return False


def detect_context(env=None, alive=None):
    """zellij, agterm (socket env set), remote (SSH without agterm) or plain.

    Inside a Zellij pane (ZELLIJ and ZELLIJ_PANE_ID) the zellij context wins, unless agterm
    variables are present and agtermctl confirms that agterm session is alive: a Zellij server
    started from an agterm shell passes stale agterm variables to every pane. Otherwise agterm
    wins when both are present, since its variables are not forwarded over SSH.
    Use --context if wrong.
    """
    env = os.environ if env is None else env
    agterm = env.get('AGTERM_ENABLED') == '1' or env.get('AGTERM_SESSION_ID')
    if env.get('ZELLIJ') is not None and env.get('ZELLIJ_PANE_ID'):
        if not agterm or not (alive or agterm_alive)(env):
            return 'zellij'
    if agterm:
        return 'agterm'
    if env.get('SSH_CONNECTION') or env.get('SSH_TTY'):
        return 'remote'
    return 'plain'


def help_text(context):
    lines = ['usage: agents [--context agterm|zellij|remote|plain] [GROUP COMMAND ...]', '',
             'Detected context: %s. Groups for it come first.' % context, '']
    for group in RELEVANT[context]:
        purpose, commands = GROUPS[group]
        lines.append('  %-9s %s' % (group, purpose))
        if commands:
            lines.append('            ' + ' '.join(commands))
    lines += ['', 'No arguments opens the menu for this context.',
              'Group help: agents GROUP --help. The old project-session command is gone; old `agents` host commands are now `agents remote`.']
    return '\n'.join(lines)


def first_command(rest):
    """First positional token, skipping the global --config VALUE and --dry-run."""
    i = 0
    while i < len(rest):
        token = rest[i]
        if token == '--config':
            i += 2
        elif token == '--dry-run' or token.startswith('--config='):
            i += 1
        else:
            return token, i
    return None, len(rest)


def run_group(group, rest):
    if group == 'remote':
        from .agterm.remote import main as remote_main
        return remote_main(rest)
    if group == 'zellij':
        from .core.zellij import command as zellij_command
        return zellij_command(rest)
    if group == 'mobile':
        from .agterm.mobile import command as mobile_command
        return mobile_command(rest)
    if group == 'configure':
        from .core.settings import command as config_command
        return config_command(['tui'] + rest)
    command, _ = first_command(rest)
    if group == 'agent':
        if command not in AGENT_COMMANDS:
            raise ValueError('agent needs one of: ' + ', '.join(AGENT_COMMANDS))
        if command == 'notify':
            from .core.notify import command as notify_command
            return notify_command(rest[1:], prog='agents agent notify')
        if command == 'ask':
            from .core.ask import command as ask_command
            return ask_command(rest[1:])
        from .agterm.names import command as names_command
        return names_command(rest)
    if group == 'admin':
        if command not in ADMIN_COMMANDS:
            raise ValueError('admin needs one of: ' + ', '.join(ADMIN_COMMANDS))
        if command == 'config':
            from .core.settings import command as config_command
            return config_command(rest[1:])
        if command == 'skills':
            from .core.skills import command as skills_command
            return skills_command(rest[1:])
        if command == 'notify':
            from .core.notify import command as notify_command
            return notify_command(rest[1:])
        if command == 'instructions':
            from .core.instructions import command as instructions_command
            return instructions_command(rest[1:])
    elif command not in AGTERM_COMMANDS:
        raise ValueError('agterm needs one of: ' + ', '.join(AGTERM_COMMANDS))
    from .cli import main
    return main(rest)


def context_menu(context):
    """Bare run: go straight to the usual action; ask only when the context is unclear."""
    if context == 'agterm':
        return run_group('agterm', ['startup'])
    if context == 'zellij':
        return run_group('zellij', ['menu'])
    if context == 'remote':
        return run_group('remote', [])
    from .cli import pick
    choice = pick('agents', [('Open the project and agent menu (agterm)', 'agterm'),
                                  ('SSH shell or attach an agterm session', 'remote'),
                                  ('Settings and actions', 'config'),
                                  ('Show all commands', 'help')])
    if choice is None:
        return 0
    if choice == 'help':
        print(help_text(context))
        return 0
    if choice == 'config':
        return run_group('configure', [])
    return run_group(choice, ['startup'] if choice == 'agterm' else [])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    context = None
    if argv[:1] == ['--context'] or (argv and argv[0].startswith('--context=')):
        if argv[0] == '--context':
            if len(argv) < 2:
                raise ValueError('--context needs agterm, zellij, remote or plain')
            context, argv = argv[1], argv[2:]
        else:
            context, argv = argv[0].split('=', 1)[1], argv[1:]
        if context not in RELEVANT:
            raise ValueError('--context must be agterm, zellij, remote or plain')
    context = context or detect_context()
    if not argv:
        return context_menu(context)
    if argv[0] in ('-h', '--help', 'help'):
        print(help_text(context))
        return 0
    group, rest = argv[0], argv[1:]
    if group not in GROUPS:
        raise ValueError('Unknown group %r. Groups: %s. Run agents --help.' % (group, ', '.join(GROUPS)))
    return run_group(group, rest)


def entrypoint():
    from .cli import guarded
    guarded(main)


if __name__ == '__main__':
    entrypoint()
