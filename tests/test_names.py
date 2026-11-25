import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from agent_utils.agterm import names

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'agent_utils/agterm/names.py'
CONFIGURE=ROOT/'scripts/configure-session-names.py'


class NamesTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.d=Path(self.tmp.name);self.log=self.d/'log'
        fake=self.d/'agtermctl';fake.write_text('#!/bin/sh\necho "$*" >> "$FAKE_LOG"\n');fake.chmod(0o755)
        self.fake=str(fake)
        patcher=mock.patch.dict(os.environ,{'XDG_STATE_HOME':str(self.d/'state'),'XDG_CONFIG_HOME':str(self.d/'config'),'FAKE_LOG':str(self.log)})
        patcher.start();self.addCleanup(patcher.stop)

    def env(self,**kw):
        return {'AGTERMCTL':self.fake,'AGTERM_SESSION_ID':'T1','FAKE_LOG':str(self.log),**kw}

    def lines(self):return self.log.read_text().splitlines() if self.log.exists() else []

    def test_detect_and_clean(self):
        self.assertEqual(names.detect({'KIRO_SESSION_ID':'k'}),('kiro-cli','k'))
        self.assertEqual(names.detect({'CODEX_THREAD_ID':'c'}),('codex','c'))
        self.assertEqual(names.detect({'CLAUDECODE':'1'}),('claude',None))
        self.assertEqual(names.detect({'CLAUDECODE':'1','AGENT_UTILS_CLAUDE_SESSION_ID':'x'}),('claude','x'))
        self.assertEqual(names.detect({}),(None,None))
        self.assertEqual(names.clean('  Topic,\n action '),'Topic, action')
        for bad in ('','x'*101,'a "b"'):
            with self.assertRaises(ValueError):names.clean(bad)

    def test_rename_kiro_reports_tool(self):
        report=dict(names.rename('Topic, action',env=self.env(KIRO_SESSION_ID='k1')))
        self.assertEqual(report['agterm'],'ok')
        self.assertIn('update_session_information',report['kiro-cli'])
        self.assertEqual(self.lines(),['session rename Topic, action --target T1'])

    def test_rename_outside_agterm_and_unknown_agent(self):
        report=dict(names.rename('Topic',env={}))
        self.assertTrue(report['agterm'].startswith('skipped'))
        self.assertTrue(report['agent'].startswith('skipped'))

    def test_rename_claude_without_hook_session(self):
        report=dict(names.rename('Topic',env=self.env(CLAUDECODE='1')))
        self.assertIn('configure-session-names',report['claude'])

    def test_rename_codex_calls_app_server(self):
        with mock.patch.object(names,'codex_set') as setter:
            report=dict(names.rename('Topic',env=self.env(CODEX_THREAD_ID='c1')))
        setter.assert_called_once_with('c1','Topic');self.assertEqual(report['codex'],'ok')

    def test_rename_other_agent_is_agterm_only(self):
        report=dict(names.rename('Topic',env=self.env(VIBE_SESSION='1')))
        self.assertIn('agterm only',report['vibe'])

    def test_claude_pending_applied_at_next_prompt(self):
        env=self.env(AGENT_UTILS_CLAUDE_SESSION_ID='s1')
        self.assertTrue(dict(names.rename('New topic',env=env))['claude'].startswith('pending'))
        out=json.loads(names.hook('claude','prompt',{'session_id':'s1','session_title':'Old'},env))
        self.assertEqual(out['hookSpecificOutput'],{'hookEventName':'UserPromptSubmit','sessionTitle':'New topic'})
        # The stale old title on the next prompt must not undo the rename.
        self.assertEqual(names.hook('claude','prompt',{'session_id':'s1','session_title':'New topic'},env),'')
        self.assertEqual(self.lines(),['session rename New topic --target T1'])

    def test_claude_start_exports_id_and_copies_title(self):
        envfile=self.d/'claude.env'
        env=self.env(CLAUDE_ENV_FILE=str(envfile))
        self.assertEqual(names.hook('claude','start',{'session_id':'s2','session_title':'Saved name'},env),'')
        self.assertEqual(envfile.read_text(),'export AGENT_UTILS_CLAUDE_SESSION_ID=s2\n')
        self.assertEqual(self.lines(),['session rename Saved name --target T1'])
        names.hook('claude','prompt',{'session_id':'s2','session_title':'Saved name'},env)
        self.assertEqual(len(self.lines()),1)
        names.hook('claude','prompt',{'session_id':'s2','session_title':'Renamed in Claude'},env)
        self.assertEqual(self.lines()[-1],'session rename Renamed in Claude --target T1')

    def test_resume_in_new_pane_copies_again(self):
        names.hook('claude','prompt',{'session_id':'s3','session_title':'Name'},self.env())
        names.hook('claude','prompt',{'session_id':'s3','session_title':'Name'},self.env(AGTERM_SESSION_ID='T2'))
        self.assertEqual(self.lines(),['session rename Name --target T1','session rename Name --target T2'])

    def test_codex_and_kiro_names(self):
        codex=self.d/'codex';codex.mkdir()
        (codex/'session_index.jsonl').write_text('{"id":"c1","thread_name":"Old"}\n{"id":"c2","thread_name":"Other"}\n{"id":"c1","thread_name":"New"}\n')
        self.assertEqual(names.codex_name('c1',codex),'New');self.assertIsNone(names.codex_name('zz',codex))
        session=self.d/'.kiro/sessions/abc/sess_1';session.mkdir(parents=True)
        (session/'session.json').write_text('{"title":"Kiro title"}')
        self.assertEqual(names.kiro_name('sess_1',self.d),'Kiro title')
        with mock.patch.object(names,'kiro_name',return_value='Kiro title'):
            names.hook('kiro-cli','start',{},self.env(KIRO_SESSION_ID='sess_1'))
        self.assertEqual(self.lines(),['session rename Kiro title --target T1'])

    def test_reminder_until_named_then_every_nth_prompt(self):
        env=self.env(KIRO_SESSION_ID='sess_2')
        with mock.patch.object(names,'kiro_name',return_value=None):
            first=names.hook('kiro-cli','prompt',{},env)
            self.assertIn('agents agent rename',first);self.assertIn('update_session_information',first)
            self.assertEqual(names.hook('kiro-cli','start',{},env),'')
            self.assertEqual(names.hook('kiro-cli','prompt',{},{k:v for k,v in env.items() if k!='AGTERM_SESSION_ID'}),'')
            names.rename('Topic, assess','kiro-cli','sess_2',env)
            outs=[names.hook('kiro-cli','prompt',{},env) for _ in range(names.REMIND_EVERY)]
        self.assertEqual(outs[:-1],['']*(names.REMIND_EVERY-1))
        self.assertIn('Topic, assess',outs[-1])

    def test_claude_reminder_is_additional_context(self):
        out=json.loads(names.hook('claude','prompt',{'session_id':'c1'},self.env()))
        self.assertIn('agents agent rename',out['hookSpecificOutput']['additionalContext'])

    def test_hook_script_is_silent_and_exits_zero(self):
        env={**{k:v for k,v in os.environ.items() if not k.startswith(('AGTERM','KIRO','CODEX','CLAUDE'))},**self.env()}
        names.mark_named('claude','s9','Named',env)
        for stdin in ('not json','{"session_id":"s9","session_title":"T"}'):
            done=subprocess.run([sys.executable,str(SCRIPT),'hook','claude','prompt'],input=stdin,text=True,capture_output=True,env=env)
            self.assertEqual((done.returncode,done.stdout,done.stderr),(0,'',''))
        self.assertEqual(self.lines(),['session rename T --target T1'])


class ConfigureTests(unittest.TestCase):
    def test_apply_and_remove_keep_other_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            home=Path(tmp);settings=home/'.claude/settings.json';settings.parent.mkdir()
            other={'hooks':{'UserPromptSubmit':[{'hooks':[{'type':'command','command':'other'}]}]},'model':'x'}
            settings.write_text(json.dumps(other))
            run=lambda *a:subprocess.run([sys.executable,str(CONFIGURE),'--home',str(home),'--all',*a],capture_output=True,text=True)
            preview=run();self.assertEqual(preview.returncode,0,preview.stderr);self.assertIn('Preview only',preview.stdout)
            self.assertEqual(json.loads(settings.read_text()),other)
            self.assertEqual(run('--apply').returncode,0)
            data=json.loads(settings.read_text())
            self.assertEqual(data['hooks']['UserPromptSubmit'][0],other['hooks']['UserPromptSubmit'][0])
            self.assertTrue(data['hooks']['UserPromptSubmit'][1]['hooks'][0]['command'].endswith('names.py hook claude prompt'))
            self.assertTrue(data['hooks']['SessionStart'][0]['hooks'][0]['command'].endswith('names.py hook claude start'))
            codex=json.loads((home/'.codex/hooks.json').read_text())
            self.assertEqual(codex['hooks']['SessionStart'][0]['matcher'],'startup|resume|fork|clear')
            self.assertTrue((home/'.kiro/hooks/agent-utils-session-names.json').exists())
            self.assertTrue((home/'.pi/agent/extensions/agent-utils-session-name.ts').is_symlink())
            self.assertIn('Already up to date',run('--apply').stdout)
            self.assertEqual(run('--apply','--remove').returncode,0)
            self.assertEqual(json.loads(settings.read_text()),other)
            self.assertFalse((home/'.kiro/hooks/agent-utils-session-names.json').exists())
            self.assertFalse((home/'.pi/agent/extensions/agent-utils-session-name.ts').is_symlink())


if __name__=='__main__':
    unittest.main()
