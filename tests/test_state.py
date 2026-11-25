import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from argparse import Namespace
from agent_utils.cli import launch_coordinator,load,atomic_write
from agent_utils.agterm.state import locked_registry

class CoordinatorTests(unittest.TestCase):
    def test_register_then_focus(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}),patch('shutil.which',return_value='/bin/claude'):
            ag=Mock(dry=False);ag.window.return_value='window';ag.launch.return_value='row'
            ag.call.return_value={'workspaces':[]}
            project={'id':'test','name':'Test','roots':[d]}
            args=Namespace(agent=None,session=None,prompt=None,dry_run=False)
            launch_coordinator(ag,{'name':'Team'},project,args)
            with locked_registry() as registry:
                record=registry['coordinators']['test'];self.assertEqual(record['terminal_id'],'row')
                self.assertTrue(record['conversation_id'])
            command=ag.launch.call_args[0][2];self.assertIn('--session-id',command)
            ag.call.return_value={'workspaces':[{'sessions':[{'id':'row'}]}]}
            launch_coordinator(ag,{'name':'Team'},project,args)
            self.assertEqual(ag.launch.call_count,1)
            self.assertEqual(ag.call.call_args[0],('session','select','--target','row','--window','window'))
    def test_launch_failure_not_registered(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}),patch('shutil.which',return_value='/bin/claude'):
            ag=Mock(dry=False);ag.call.return_value={'workspaces':[]};ag.launch.side_effect=OSError('failed')
            with self.assertRaises(OSError):launch_coordinator(ag,{'name':'Team'},{'id':'test','name':'Test','roots':[d]},Namespace(agent=None,session=None,prompt=None,dry_run=False))
            self.assertFalse((Path(d)/'agent-utils/registry.json').exists())
    def test_roots_string_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';atomic_write(p,{'version':1,'windows':[{'name':'Team','default_agent':'claude','projects':[{'id':'a','name':'A','roots':'/tmp'}]}]})
            with self.assertRaises(ValueError):load(p)
