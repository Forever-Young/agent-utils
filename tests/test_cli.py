import copy
import json
from pathlib import Path
import tempfile
import os
import unittest
from unittest.mock import patch
from agent_utils.cli import load, agent_argv, main, atomic_write

class Tests(unittest.TestCase):
    def config(self):
        return {'version':1,'windows':[{'name':'Team','default_agent':'codex','projects':[{'id':'demo','name':'Demo','roots':['/tmp/project with space', '/tmp/extra;$(echo nope)']}]}]}
    def test_unique_projects(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';x=self.config();x['windows'][0]['projects']*=2;atomic_write(p,x)
            with self.assertRaises(ValueError):load(p)
    def test_roots_remain_arguments(self):
        p=self.config()['windows'][0]['projects'][0]
        a,cwd=agent_argv(p,'codex','exact-id')
        self.assertEqual(a[1:3],['resume','exact-id'])
        self.assertEqual(a[-2:],['--add-dir','/tmp/extra;$(echo nope)'])
    def test_coordinator_uses_sonnet_for_new_and_resumed_processes(self):
        p=self.config()['windows'][0]['projects'][0]
        p['agent_args']={'claude':['--model','haiku']}
        for resume in (None,'exact-id'):
            with self.subTest(resume=resume):
                argv,_=agent_argv(p,'claude',resume,coordinator=True)
                models=[argv[i+1] for i,arg in enumerate(argv) if arg=='--model']
                self.assertEqual(models[-1],'sonnet')
                self.assertEqual(argv[argv.index('--system-prompt-snapshot')+1],'off')
                prompt=argv[argv.index('--append-system-prompt')+1]
                self.assertIn('You are a dispatcher and nothing else.',prompt)
                self.assertIn('Never run two workers for the same task.',prompt)
        argv,_=agent_argv(p,'claude')
        self.assertEqual(argv[-2:],['--model','haiku'])
    def test_dry_run_never_runs_command(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}):
            p=Path(d)/'config.json';atomic_write(p,self.config())
            with patch('agent_utils.cli.run',return_value=json.dumps({'result':{'windows':[]}})) as run:
                main(['--config',str(p),'--dry-run','new','--project','demo'])
                self.assertEqual([call.args[0][1:3] for call in run.call_args_list],[['window','list']])
    def test_dirty_worktree_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'config.json';atomic_write(p,self.config())
            with patch('agent_utils.cli.run',side_effect=['ok','modified']):
                with self.assertRaisesRegex(ValueError,'Dirty'):
                    main(['--config',str(p),'worktree','--project','demo','--repo',d,'--task','issue-1','--destination',str(Path(d)/'target')])
    def test_atomic_config(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'nested/config.json';atomic_write(p,self.config());self.assertEqual(load(p),self.config())
if __name__=='__main__':unittest.main()
