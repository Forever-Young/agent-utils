import io
import json
import os
import shlex
import subprocess
import unittest
from unittest.mock import patch

from agent_utils.agterm import coordinator_gate as gate
from agent_utils.cli import agent_argv


def denied(tool, **inp):
    return gate.decide({'tool_name': tool, 'tool_input': inp}) is not None


class GateTests(unittest.TestCase):
    def test_work_tools_are_denied(self):
        for tool in ('NotebookEdit', 'Skill', 'Agent', 'Grep', 'Glob', 'WebFetch', 'mcp__atlassian__getJiraIssue'):
            self.assertTrue(denied(tool), tool)
        self.assertTrue(denied('Edit', file_path='/p/src/app.py'))
        self.assertTrue(denied('Read', file_path='/p/src/app.py'))

    def test_dispatch_tools_are_allowed(self):
        self.assertFalse(denied('Edit', file_path='/p/tasks/KEY-1.md'))
        self.assertFalse(denied('Read', file_path='/p/AGENTS.md'))
        self.assertFalse(denied('SendMessage'))
        self.assertFalse(denied('ListAgents'))
        self.assertFalse(denied('mcp__plannotator__plannotator_inbox'))

    def test_bash_allowlist(self):
        for ok in ('agents agterm list', 'agtermctl tree', 'ps aux',
                   'cat tasks/K-1.md', 'git status', 'git worktree list', 'echo hi | pbcopy'):
            self.assertFalse(denied('Bash', command=ok), ok)
        for bad in ('pytest', 'cat src/app.py', 'git commit -m x', 'ls; rm -rf x', 'echo x > f',
                    'ls $(whoami)', 'git push', 'agents agterm list && pytest', 'cat'):
            self.assertTrue(denied('Bash', command=bad), bad)

    def test_clipboard_stdin_and_readback(self):
        for command in ('pbpaste', 'pbcopy < tasks/prompt.txt',
                        'pbcopy < /p/tmp/draft.txt', 'cat tasks/prompt.txt | pbcopy',
                        "printf '%s' 'a prompt with ; punctuation' | pbcopy",
                        'agents agterm new --prompt "Please check A; then B" --send'):
            self.assertFalse(denied('Bash', command=command), command)
        for command in ('pbcopy < src/private.txt', 'pbcopy < tasks/../secret',
                        'pbcopy < tasks/a > tmp/b', 'cat src/a | pbcopy',
                        'ls | cat tasks/a', 'echo hi | pbcopy; ls',
                        'agents agterm list\npwd', 'echo $(pwd) | pbcopy'):
            self.assertTrue(denied('Bash', command=command), command)

    def test_hook_output_and_role_scope(self):
        event = json.dumps({'tool_name': 'Skill', 'tool_input': {}})
        with patch('sys.stdin', io.StringIO(event)), patch('sys.stdout', new_callable=io.StringIO) as out:
            gate.main()
        self.assertEqual(json.loads(out.getvalue())['hookSpecificOutput']['permissionDecision'], 'deny')
        with patch.dict(os.environ, {'AGTERM_UTILS_ROLE': 'worker'}), patch('sys.stdin', io.StringIO(event)), \
                patch('sys.stdout', new_callable=io.StringIO) as out:
            gate.main()
        self.assertEqual(out.getvalue(), '')

    def test_only_coordinator_launch_gets_the_hook(self):
        p = {'id': 'x', 'name': 'X', 'roots': ['/tmp']}
        argv, _ = agent_argv(p, 'claude', coordinator=True)
        settings = json.loads(argv[argv.index('--settings') + 1])
        command = settings['hooks']['PreToolUse'][0]['hooks'][0]['command']
        # Must run from any interpreter, not only one that can import the package.
        result = subprocess.run(shlex.split(command), input=json.dumps({'tool_name': 'Skill'}),
                                capture_output=True, text=True, cwd='/', env={'PATH': os.environ['PATH']})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('deny', result.stdout)
        self.assertNotIn('--settings', agent_argv(p, 'claude')[0])


if __name__ == '__main__':
    unittest.main()
