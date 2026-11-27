"""Documentation checks: every command, subcommand, option and environment
variable has an entry in docs/reference, and every relative Markdown link resolves.

The parsers are captured by intercepting ArgumentParser.parse_args, so the check
follows the code instead of a copied list of commands.
"""
import argparse
import importlib
import importlib.util
import re
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_utils import entry

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'docs' / 'reference'


class Captured(Exception):
    def __init__(self, parser):
        super().__init__(parser.prog)
        self.parser = parser


def _raise(self, *args, **kwargs):
    raise Captured(self)


def capture(call):
    with patch.object(argparse.ArgumentParser, 'parse_args', _raise), \
            patch.object(argparse.ArgumentParser, 'parse_known_args', _raise):
        try:
            call()
        except Captured as captured:
            return captured.parser
    raise AssertionError('no parser reached')


def nodes(prefix, parser):
    """(command path, options, positional choices) for a parser and its subparsers."""
    options, choices, children = [], [], {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            children.update(action.choices)
        elif action.option_strings:
            if '-h' not in action.option_strings:
                options.append(max(action.option_strings, key=len))
        elif action.choices:
            choices.append((action.dest, list(action.choices)))
    yield prefix, options, choices
    for name, child in children.items():
        yield from nodes(prefix + ' ' + name, child)


# (argv that reaches the parser, command path shown in the docs, reference file)
GROUP_PARSERS = [
    (['remote'], 'agents remote', 'remote.md'),
    (['mobile'], 'agents mobile', 'mobile.md'),
    (['configure'], 'agents admin config', 'admin.md'),
    (['agent', 'rename'], 'agents agent', 'agent.md'),
    (['agent', 'ask', 'x'], 'agents agent ask', 'agent.md'),
    (['agent', 'ask', 'check'], 'agents agent ask check', 'agent.md'),
    (['agent', 'notify'], 'agents agent notify', 'agent.md'),
    (['admin', 'skills'], 'agents admin skills', 'admin.md'),
    (['admin', 'skills', 'audit'], 'agents admin skills', 'admin.md'),
    (['admin', 'skills', 'schedule'], 'agents admin skills schedule', 'admin.md'),
    (['admin', 'instructions'], 'agents admin instructions', 'admin.md'),
    (['admin', 'instructions', 'compose'], 'agents admin instructions compose', 'admin.md'),
    (['admin', 'notify'], 'agents admin notify', 'admin.md'),
]

# The flat `agents agterm` parser takes COMMAND [OPERATION]. Operations per command,
# so a new operation must be added here and documented.
AGTERM_OPERATIONS = {
    'session': ['list', 'focus', 'resume', 'hide', 'unhide', 'archive', 'unarchive', 'forget', 'bind',
                'status', 'finish', 'provider-archive', 'provider-unarchive', 'provider-delete'],
    'task': ['bind', 'rename'],
    'restore': ['sync', 'snapshot', 'plan', 'pin'],
    'project': ['add', 'edit', 'enable', 'disable', 'move', 'order', 'push-order', 'pull-order'],
    'settings': ['list'],
    'inbox': ['doctor', 'threads', 'dispatch', 'launches', 'adopt', 'rename', 'subject', 'reopen',
              'author', 'withdraw', 'move', 'relay'],
}
ADMIN_VIA_AGTERM = ('project', 'settings', 'inbox')

SETUP_MODULES = ['installer', 'kiro', 'relay', 'adopt_job', 'session_names', 'zellij_status', 'zsh', 'startup', 'optional']


def read(name):
    return (REFERENCE / name).read_text()


def mentions(text, token):
    return re.search(r'(?<![\w-])' + re.escape(token) + r'(?![\w-])', text) is not None


class ReferenceTests(unittest.TestCase):
    def test_group_commands_and_options(self):
        missing = []
        for argv, prefix, doc in GROUP_PARSERS:
            text = read(doc)
            parser = capture(lambda: entry.main(list(argv)))
            for path, options, choices in nodes(prefix, parser):
                if path != prefix and path not in text:
                    missing.append('%s: command %s' % (doc, path))
                for option in options:
                    if not mentions(text, option):
                        missing.append('%s: %s option %s' % (doc, path, option))
                for dest, values in choices:
                    for value in values:
                        if not mentions(text, str(value)):
                            missing.append('%s: %s %s value %s' % (doc, path, dest, value))
        self.assertEqual(missing, [])

    def test_agterm_flat_parser(self):
        parser = capture(lambda: entry.main(['agterm', 'list']))
        actions = {a.dest: a for a in parser._actions}
        commands = list(actions['command'].choices)
        operations = set(actions['operation'].choices)
        self.assertEqual(operations, {op for ops in AGTERM_OPERATIONS.values() for op in ops},
                         'update AGTERM_OPERATIONS and the reference for a new operation')
        agterm, admin = read('agterm.md'), read('admin.md')
        missing = []
        for command in commands:
            doc, text, group = ('admin.md', admin, 'admin') if command in ADMIN_VIA_AGTERM else ('agterm.md', agterm, 'agterm')
            if 'agents %s %s' % (group, command) not in text:
                missing.append('%s: command agents %s %s' % (doc, group, command))
            for op in AGTERM_OPERATIONS.get(command, []):
                if 'agents %s %s %s' % (group, command, op) not in text:
                    missing.append('%s: command agents %s %s %s' % (doc, group, command, op))
        for action in parser._actions:
            if action.option_strings and '-h' not in action.option_strings:
                option = max(action.option_strings, key=len)
                if not (mentions(agterm, option) or mentions(admin, option)):
                    missing.append('agterm.md/admin.md: option ' + option)
        self.assertEqual(missing, [])

    def test_every_group_has_a_reference_entry(self):
        index = read('README.md')
        for group in entry.GROUPS:
            self.assertTrue(mentions(index, 'agents ' + group) or ('`agents %s`' % group) in index, group)
        for name, (_, commands) in entry.GROUPS.items():
            doc = {'configure': 'admin.md'}.get(name, name + '.md')
            text = read(doc) if name != 'agterm' else read('agterm.md') + read('admin.md')
            for command in commands:
                self.assertIn('agents %s %s' % (name, command), text, (doc, command))

    def test_setup_modules(self):
        text = read('setup.md')
        missing = []
        for name in SETUP_MODULES:
            module = importlib.import_module('agent_utils.core.setup.' + name)
            if 'agent_utils.core.setup.' + name not in text:
                missing.append('module ' + name)
            for _, options, _ in nodes(name, capture(lambda: module.main([]))):
                missing += ['%s option %s' % (name, o) for o in options if not mentions(text, o)]
        spec = importlib.util.spec_from_file_location('brew_local', ROOT / 'scripts' / 'brew-local.py')
        brew = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(brew)
        for _, options, _ in nodes('brew', capture(lambda: brew.main([]))):
            missing += ['brew-local option ' + o for o in options if not mentions(text, o)]
        spec = importlib.util.spec_from_file_location('release', ROOT / 'scripts' / 'release.py')
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        for _, options, _ in nodes('release', capture(lambda: release.main([]))):
            missing += ['release option ' + o for o in options if not mentions(text, o)]
        for script in sorted((ROOT / 'scripts').glob('*.py')):
            if 'scripts/' + script.name not in text:
                missing.append('script ' + script.name)
        self.assertEqual(missing, [])

    def test_environment_variables(self):
        names = set()
        for path in (ROOT / 'agent_utils').rglob('*.py'):
            names |= set(re.findall(r'\b(?:AGTERM_UTILS|AGENT_UTILS)_[A-Z][A-Z_]*[A-Z]\b', path.read_text()))
        text = read('files.md')
        self.assertEqual(sorted(n for n in names if n not in text), [])


class LinkTests(unittest.TestCase):
    def test_relative_links_resolve(self):
        files = [ROOT / 'README.md', *(ROOT / 'docs' / 'guide').glob('*.md'),
                 *REFERENCE.glob('*.md'), *(ROOT / 'docs' / 'review').glob('*.md')]
        broken = []
        for path in files:
            for target in re.findall(r'\]\(([^)#\s]+)(?:#[^)]*)?\)', path.read_text()):
                if re.match(r'[a-z]+:', target):
                    continue
                if not (path.parent / target).exists():
                    broken.append('%s -> %s' % (path.relative_to(ROOT), target))
        self.assertEqual(broken, [])

    def test_guides_exist_and_are_linked_from_readme(self):
        readme = (ROOT / 'README.md').read_text()
        guides = sorted((ROOT / 'docs' / 'guide').glob('*.md'))
        self.assertTrue(guides)
        for path in guides + sorted(REFERENCE.glob('*.md')):
            self.assertIn(str(path.relative_to(ROOT)), readme, path.name)


if __name__ == '__main__':
    unittest.main()
