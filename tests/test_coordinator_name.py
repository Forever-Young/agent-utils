import unittest
from agent_utils.cli import agent_argv, validate


class CoordinatorName(unittest.TestCase):
    def test_validate_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate({'version': 1, 'windows': [], 'coordinator_name': ' '})

    def test_name_flag(self):
        argv, _ = agent_argv({'name': 'P', 'roots': ['/tmp'], 'session_title': 'Boss'}, 'claude', coordinator=True)
        self.assertEqual(argv[argv.index('--name') + 1], 'Boss')


if __name__ == '__main__':
    unittest.main()


import json
import os
import tempfile
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from agent_utils.agterm import coordinator_agents as ca, coordinator_gate as gate
from agent_utils.agterm.state import locked_registry
from agent_utils.cli import COORDINATOR, launch_coordinator


class OtherAgentCoordinators(unittest.TestCase):
    project = {'name': 'P', 'roots': ['/tmp'], 'id': 'p'}

    def test_codex_gets_prompt_and_gate_per_launch(self):
        argv, _ = agent_argv(self.project, 'codex', coordinator=True)
        joined = ' '.join(argv)
        self.assertIn('developer_instructions=', joined)
        self.assertIn('hooks.PreToolUse=', joined)
        self.assertIn('THE RULE', joined)

    def test_kiro_uses_generated_agent_with_hook(self):
        argv, _ = agent_argv(self.project, 'kiro-cli', coordinator=True)
        self.assertEqual(argv[argv.index('--agent') + 1], ca.KIRO_AGENT)
        cfg = ca.kiro_config(COORDINATOR)
        self.assertIn('--format kiro', cfg['hooks']['preToolUse'][0]['command'])
        self.assertNotIn('execute_bash', cfg['allowedTools'])

    def test_kiro_agent_written_once(self):
        with tempfile.TemporaryDirectory() as d:
            path = ca.write_kiro_agent('x', home=d)
            self.assertEqual(json.loads(path.read_text())['name'], ca.KIRO_AGENT)
            mtime = path.stat().st_mtime_ns
            ca.write_kiro_agent('x', home=d)
            self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_gate_understands_codex_and_kiro_events(self):
        self.assertIsNone(gate.decide({'tool_name': 'Bash', 'tool_input': {'command': 'agents agterm list'}}))
        self.assertIsNotNone(gate.decide({'tool_name': 'execute_bash', 'tool_input': {'command': 'rm -rf x'}}))
        self.assertIsNotNone(gate.decide({'tool_name': 'apply_patch', 'tool_input': {'command': '*** Begin Patch\n*** Update File: src/a.py\n'}}))
        self.assertIsNone(gate.decide({'tool_name': 'apply_patch', 'tool_input': {'command': '*** Add File: tasks/K-1.md\n'}}))
        self.assertIsNotNone(gate.decide({'tool_name': 'fs_write', 'tool_input': {'path': 'src/a.py'}}))
        self.assertIsNotNone(gate.decide({'tool_name': 'fs_read', 'tool_input': {'operations': [{'path': 'src/a.py'}]}}))
        self.assertIsNone(gate.decide({'tool_name': 'fs_read', 'tool_input': {'operations': [{'path': 'AGENTS.md'}]}}))
        self.assertIsNotNone(gate.decide({'tool_name': 'grep', 'tool_input': {'pattern': 'x'}}))

    def test_codex_coordinator_registered_with_agent_and_no_session_id(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'XDG_STATE_HOME': d, 'XDG_CONFIG_HOME': d}), patch('shutil.which', return_value='/bin/codex'):
            ag = Mock(dry=False); ag.window.return_value = 'w'; ag.launch.return_value = 'row'
            ag.call.return_value = {'workspaces': []}
            args = Namespace(agent='codex', session=None, prompt=None, dry_run=False)
            launch_coordinator(ag, {'name': 'T'}, {'id': 'p', 'name': 'P', 'roots': [d]}, args)
            command = ag.launch.call_args[0][2]
            self.assertNotIn('--session-id', command)
            with locked_registry() as registry:
                self.assertEqual(registry['coordinators']['p']['agent'], 'codex')
            with self.assertRaisesRegex(ValueError, 'forget it'):
                launch_coordinator(ag, {'name': 'T'}, {'id': 'p', 'name': 'P', 'roots': [d]}, Namespace(agent='claude', session=None, prompt=None, dry_run=False))
