import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
from agent_utils.core import notify


class Handler(BaseHTTPRequestHandler):
    got=[]
    def do_POST(self):
        Handler.got.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
        self.send_response(200);self.end_headers()
    def log_message(self,*a):pass


REAL_IDLE_SECONDS=notify.idle_seconds
REAL_SCREEN_LOCKED=notify.screen_locked


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        patcher=mock.patch.dict(os.environ,{'XDG_CONFIG_HOME':self.tmp.name});patcher.start();self.addCleanup(patcher.stop)
        Handler.got=[]
        idle=mock.patch.object(notify,'idle_seconds',return_value=1e9);idle.start();self.addCleanup(idle.stop)  # away
        lock=mock.patch.object(notify,'screen_locked',return_value=False);lock.start();self.addCleanup(lock.stop)
        self.server=HTTPServer(('127.0.0.1',0),Handler);self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever,daemon=True).start();self.addCleanup(self.server.shutdown)
        self.url='http://127.0.0.1:%d/my-topic'%self.server.server_port
    def run_cmd(self,*argv):
        out=io.StringIO()
        with redirect_stdout(out):code=notify.command(list(argv))
        return code,out.getvalue()
    def test_mobile_click_url_is_optional(self):
        self.run_cmd('url', self.url); self.run_cmd('enable')
        self.run_cmd('mobile-host', 'demo.tailnet')
        with mock.patch.dict(os.environ, {'AGTERM_SESSION_ID': 'demo-session'}):
            self.run_cmd('send', '--agent', 'codex', '--session', 'Demo', '--event', 'done')
        self.assertEqual(Handler.got[-1][1]['click'], 'agterm-mobile://demo.tailnet/session/demo-session')
    def test_render(self):
        cfg=notify.load()
        self.assertEqual(notify.render(cfg,'kiro','Plan 20','done'),('Plan 20','Done',3))
        self.assertEqual(notify.render(cfg,'claude','S','input','which db?'),('S','Input needed: which db?',4))
        with self.assertRaises(ValueError):notify.render(cfg,'a','s','bogus')
    def test_disabled_by_default_sends_nothing(self):
        self.assertEqual(notify.send('kiro','s','done'),'disabled');self.assertEqual(Handler.got,[])
    def test_enable_needs_url_and_sends(self):
        with self.assertRaises(ValueError):self.run_cmd('enable')
        self.run_cmd('url',self.url);self.run_cmd('enable')
        self.run_cmd('send','--agent','kiro','--session','Plan ü','--event','failed','--detail','tests')
        self.assertEqual(Handler.got,[('/',{'topic':'my-topic','title':'Plan ü','message':'Failed: tests','priority':4,'tags':['failed']})])
        self.run_cmd('disable');self.run_cmd('send','--agent','kiro','--session','s','--event','done')
        self.assertEqual(len(Handler.got),1)
    def test_skipped_while_at_the_computer(self):
        self.run_cmd('url',self.url);self.run_cmd('enable')
        with mock.patch.object(notify,'idle_seconds',return_value=5.0):
            code,out=self.run_cmd('send','--agent','kiro','--session','s','--event','done')
            self.assertEqual((code,out,Handler.got),(0,'',[]))  # silent, nothing sent
            self.assertIn('would skip: at the computer',self.run_cmd('--dry-run','send','--agent','k','--session','s','--event','done')[1])
            self.run_cmd('test');self.assertEqual(len(Handler.got),1)  # the test event ignores the check
        with mock.patch.object(notify,'idle_seconds',return_value=500.0):
            self.run_cmd('send','--agent','kiro','--session','s','--event','done')
        self.assertEqual(len(Handler.got),2)
    def test_away_after_setting(self):
        self.run_cmd('url',self.url);self.run_cmd('enable')
        self.assertEqual(notify.load()['away_after_seconds'],120)
        self.run_cmd('away-after','0')
        with mock.patch.object(notify,'idle_seconds',return_value=1.0):
            self.run_cmd('send','--agent','kiro','--session','s','--event','done')
        self.assertEqual(len(Handler.got),1)
        for bad in ('-1','86401'):
            with self.assertRaises(ValueError):self.run_cmd('away-after',bad)
    def test_unreadable_idle_time_never_suppresses(self):
        with mock.patch.object(notify,'idle_seconds',return_value=None):
            self.assertIsNone(notify.at_computer({'away_after_seconds':120}))
        self.assertEqual(notify.at_computer({'away_after_seconds':120},idle=3.0),3.0)
        self.assertIsNone(notify.at_computer({'away_after_seconds':120},idle=120.0))
        self.assertIsNone(notify.at_computer({'away_after_seconds':True},idle=1.0))
    def test_locked_screen_counts_as_away(self):
        cfg={'away_after_seconds':120,'away_when_locked':True}
        self.assertIsNone(notify.at_computer(cfg,idle=3.0,locked=True))
        self.assertEqual(notify.at_computer(cfg,idle=3.0,locked=False),3.0)
        self.assertEqual(notify.at_computer(cfg,idle=3.0,locked=None),3.0)  # unreadable lock state never suppresses
        self.assertEqual(notify.at_computer(dict(cfg,away_when_locked=False),idle=3.0,locked=True),3.0)
        self.assertEqual(notify.at_computer({'away_after_seconds':120},idle=3.0,locked=False),3.0)
        self.run_cmd('url',self.url);self.run_cmd('enable')
        with mock.patch.object(notify,'idle_seconds',return_value=2.0),mock.patch.object(notify,'screen_locked',return_value=True):
            self.run_cmd('send','--agent','kiro','--session','s','--event','done')
        self.assertEqual(len(Handler.got),1)  # locked: sent although the last input was 2 s ago
        self.run_cmd('away-when-locked','off');self.assertFalse(notify.load()['away_when_locked'])
        self.run_cmd('away-when-locked','on');self.assertTrue(notify.load()['away_when_locked'])
    def test_screen_locked_parses_ioreg(self):
        def run(text):
            with mock.patch.object(notify.sys,'platform','darwin'),mock.patch.object(notify.subprocess,'run',return_value=mock.Mock(stdout=text)):
                return REAL_SCREEN_LOCKED()
        self.assertTrue(run('"IOConsoleLocked" = Yes\n'))
        self.assertTrue(run('"IOConsoleLocked" = No\n"IOConsoleUsers" = ({"CGSSessionScreenIsLocked"=Yes})'))
        self.assertFalse(run('"IOConsoleLocked" = No\n'))
        self.assertIsNone(run('nothing useful'))
        with mock.patch.object(notify.sys,'platform','linux'):
            self.assertIsNone(REAL_SCREEN_LOCKED())
    def test_idle_seconds_parses_ioreg(self):
        out=mock.Mock(stdout='|   "HIDIdleTime" = 2500000000\n')
        with mock.patch.object(notify.sys,'platform','darwin'),mock.patch.object(notify.subprocess,'run',return_value=out):
            self.assertEqual(REAL_IDLE_SECONDS(),2.5)
        with mock.patch.object(notify.sys,'platform','linux'):
            self.assertIsNone(REAL_IDLE_SECONDS())
    def test_template_and_dry_run(self):
        self.run_cmd('url',self.url);self.run_cmd('enable')
        self.run_cmd('template','--title','[{agent}] {session}','--body','{event}')
        code,out=self.run_cmd('--dry-run','send','--agent','codex','--session','x','--event','done')
        self.assertIn('[codex] x | done',out);self.assertEqual(Handler.got,[])
    def test_transport_error_never_raises(self):
        self.run_cmd('url','http://127.0.0.1:9/t');self.run_cmd('enable')
        code,out=self.run_cmd('send','--agent','kiro','--session','s','--event','done')
        self.assertEqual(code,0);self.assertIn('send failed',out)
    def test_bad_url_and_template(self):
        for bad in ('ftp://x/t','https://host','nonsense'):
            with self.assertRaises(ValueError):self.run_cmd('url',bad)
        with self.assertRaises(ValueError):self.run_cmd('template')
    def test_hook_adapters(self):
        self.assertEqual(notify.parse_hook('claude','{"hook_event_name":"Stop"}'),('done',''))
        self.assertEqual(notify.parse_hook('claude','{"hook_event_name":"Notification","message":"Allow?"}'),('input','Allow?'))
        self.assertIsNone(notify.parse_hook('claude','{"hook_event_name":"PreToolUse"}'))
        self.assertEqual(notify.parse_hook('codex','{"type":"agent-turn-complete"}'),('done',''))
        self.assertIsNone(notify.parse_hook('codex','nope'))
    def test_session_name_lookup(self):
        tree={'workspaces':[{'sessions':[{'id':'s1','name':'Topic, plan'}]}]}
        self.assertEqual(notify.find_session_name('s1',tree),'Topic, plan');self.assertIsNone(notify.find_session_name('s2',tree))
    def test_kiro_hook_pushes_outside_agterm(self):
        self.run_cmd('url',self.url);self.run_cmd('enable');self.run_cmd('away-after','0')  # the hook runs as a subprocess
        env={k:v for k,v in os.environ.items() if not k.startswith('AGTERM')};env['AGENT_UTILS_SESSION_NAME']='Kiro topic'
        hook=ROOT/'agent_utils/agterm/kiro_hook.py'
        for action in ('attention','completed','active'):
            r=subprocess.run([sys.executable,str(hook),action],input='{"tool_name":"shell"}',text=True,capture_output=True,env=env)
            self.assertEqual((r.returncode,r.stdout),(0,''))
        self.assertEqual([(g['title'],g['message']) for _,g in Handler.got],[('Kiro topic','Input needed: shell'),('Kiro topic','Done')])

if __name__=='__main__':unittest.main()
