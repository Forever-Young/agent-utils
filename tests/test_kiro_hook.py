import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
HOOK=ROOT/'agent_utils/agterm/kiro_hook.py'
INSTALL=ROOT/'scripts/configure-kiro.py'

class KiroHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.d=Path(self.tmp.name);self.log=self.d/'log'
        fake=self.d/'agtermctl';fake.write_text('#!/bin/sh\necho "$*" >> "$FAKE_LOG"\n');fake.chmod(0o755)
        self.fake=fake
    def env(self,**kw):
        e={k:v for k,v in os.environ.items() if not k.startswith('AGTERM')}
        e.update(FAKE_LOG=str(self.log),XDG_CONFIG_HOME=str(self.d/'xc'),XDG_STATE_HOME=str(self.d/'xs'))
        e.update(kw);return e
    def run_hook(self,action,stdin='{"tool_name":"shell"}',**kw):
        return subprocess.run([sys.executable,str(HOOK),action],input=stdin,text=True,capture_output=True,env=self.env(**kw))
    def lines(self):return self.log.read_text().splitlines() if self.log.exists() else []
    def test_attention(self):
        r=self.run_hook('attention',AGTERMCTL=str(self.fake),AGTERM_SESSION_ID='s1',AGTERM_SOCKET='/x.sock',AGTERM_PANE='2')
        self.assertEqual((r.returncode,r.stdout),(0,''))
        l=self.lines()
        self.assertEqual(l[0],'session status blocked --blink --target s1 --socket /x.sock --pane 2')
        self.assertEqual(l[1],'notify Kiro needs permission: shell --title Kiro --target s1 --socket /x.sock')
    def test_active_and_completed(self):
        for act in ('active','completed'):self.run_hook(act,AGTERMCTL=str(self.fake),AGTERM_SESSION_ID='s1')
        self.assertEqual(self.lines(),['session status active --target s1','session status completed --target s1'])
    def test_noop_outside_agterm(self):
        r=self.run_hook('attention',AGTERMCTL=str(self.fake))
        self.assertEqual((r.returncode,r.stdout,self.lines()),(0,'',[]))
    def test_bad_stdin_and_missing_ctl(self):
        r=self.run_hook('attention','not json',AGTERMCTL=str(self.fake),AGTERM_SESSION_ID='s1')
        self.assertEqual(r.returncode,0);self.assertEqual(self.lines()[1],'notify Kiro needs permission --title Kiro --target s1')
        r=self.run_hook('attention',AGTERMCTL=str(self.d/'missing'),AGTERM_SESSION_ID='s1',PATH='/nonexistent')
        self.assertEqual((r.returncode,r.stdout),(0,''))
    def install(self,*args):
        return subprocess.run([sys.executable,str(INSTALL),'--home',str(self.d/'home'),*args],text=True,capture_output=True,env=self.env())
    def test_installer(self):
        target=self.d/'home/.kiro/hooks/agterm-utils-attention.json'
        r=self.install();self.assertEqual(r.returncode,0);self.assertFalse(target.exists());self.assertIn('3.4.0',r.stdout)
        self.assertEqual(self.install('--apply').returncode,0)
        cfg=json.loads(target.read_text());self.assertEqual(cfg['version'],'v1')
        got={h['trigger']:h['action']['command'] for h in cfg['hooks'] if h['name'].startswith('agterm-') and 'inbox' not in h['name']}
        self.assertEqual({h['name'] for h in cfg['hooks'] if 'inbox' in h['name']},{'agterm-inbox-start','agterm-inbox-end','agterm-inbox-learn','agterm-inbox-prompt','agterm-inbox-stop'})
        self.assertEqual(set(got),{'PermissionRequest','UserPromptSubmit','PostToolUse','Stop'})
        self.assertTrue(got['PermissionRequest'].endswith('agent_utils/agterm/kiro_hook.py attention'))
        self.install('--apply');self.assertEqual(list(target.parent.glob('*.backup-*')),[])
        target.write_text('{}');self.install('--apply')
        self.assertEqual(len(list(target.parent.glob('*.backup-*'))),1)
        self.assertEqual(self.install('--remove','--apply').returncode,0);self.assertFalse(target.exists())

if __name__=='__main__':unittest.main()
