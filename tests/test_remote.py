from argparse import Namespace
import unittest
from unittest.mock import patch
from agent_utils.agterm.remote import main,filter_sessions


class RemoteTests(unittest.TestCase):
    def test_selected_id_reaches_native_attach(self):
        with patch('agent_utils.cli.Agterm') as adapter:
            ag=adapter.return_value
            ag.call.return_value={'remote':{'sessions':[{'id':'exact','name':'Agent','panes':[]}]}}
            main(['attach','--host','demo','--session','exact','--companions'])
            self.assertEqual(ag.call.call_args.args,('zmx','attach','demo','exact','--companions'))
    def test_stale_selection_never_attaches(self):
        with patch('agent_utils.cli.Agterm') as adapter:
            adapter.return_value.call.return_value={'remote':{'sessions':[]}}
            with self.assertRaises(ValueError):main(['attach','--host','demo','--session','gone'])
            self.assertEqual(adapter.return_value.call.call_count,1)
    def test_detach_refuses_origin_row(self):
        with patch('agent_utils.cli.Agterm') as adapter:
            adapter.return_value.call.return_value={'workspaces':[{'sessions':[{'id':'origin'}]}]}
            with self.assertRaises(ValueError):main(['detach','--session','origin'])
            self.assertEqual(adapter.return_value.call.call_count,1)
    def test_attention_filters(self):
        args=Namespace(window=None,project=None,attention=True,agents_only=False,attention_first=True)
        self.assertEqual([x['id'] for x in filter_sessions([{'id':'a','status':'active'},{'id':'b','status':'blocked'}],args)],['b'])

    def terminal_inventory(self,d,daemon='daemon-left'):
        from pathlib import Path
        binary=Path(d)/'zmx';binary.touch();binary.chmod(0o700)
        return {'endpoint':{'executable':str(binary),'socketDirectory':d},'sessions':[
            {'id':'origin','name':'Task','panes':[{'pane':'left','daemon':daemon}]}]}

    def test_terminal_checks_then_attaches_with_origin_endpoint(self):
        import os,tempfile
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as d,patch('agent_utils.cli.Agterm') as adapter,patch('os.isatty',return_value=True),patch('subprocess.run',return_value=Mock(returncode=0)) as check,patch('subprocess.call',return_value=0) as attach,patch.dict(os.environ,{'ZMX_SESSION':'wrong','ZMX_SESSION_PREFIX':'wrong','ZMX_NO_DETACH_KEY':'1','AGTERM_SESSION_ID':'wrong'}):
            reply=self.terminal_inventory(d)
            adapter.return_value.call.return_value={'remote':reply}
            main(['terminal','--session','origin'])
            self.assertEqual(adapter.return_value.call.call_count,2)
            self.assertEqual(check.call_args.args[0],[d+'/zmx','get','daemon-left'])
            self.assertEqual(attach.call_args.args[0],[d+'/zmx','attach','daemon-left'])
            env=attach.call_args.kwargs['env']
            self.assertEqual(env['ZMX_DIR'],d)
            for key in ['ZMX_SESSION','ZMX_SESSION_PREFIX','ZMX_NO_DETACH_KEY','AGTERM_SESSION_ID']:self.assertNotIn(key,env)

    def test_terminal_refuses_disappeared_daemon(self):
        import tempfile
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as d,patch('agent_utils.cli.Agterm') as adapter,patch('os.isatty',return_value=True),patch('subprocess.run',return_value=Mock(returncode=1)),patch('subprocess.call') as attach:
            adapter.return_value.call.return_value={'remote':self.terminal_inventory(d)}
            with self.assertRaisesRegex(ValueError,'no longer available'):main(['terminal','--session','origin'])
            attach.assert_not_called()

    def test_terminal_refuses_changed_pane_before_existence_check(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d,patch('agent_utils.cli.Agterm') as adapter,patch('os.isatty',return_value=True),patch('subprocess.run') as check,patch('subprocess.call') as attach:
            adapter.return_value.call.side_effect=[{'remote':self.terminal_inventory(d)}, {'remote':self.terminal_inventory(d,'replacement')}]
            with self.assertRaisesRegex(ValueError,'changed'):main(['terminal','--session','origin'])
            check.assert_not_called();attach.assert_not_called()

    def test_ssh_default_enters_local_terminal_mode(self):
        import os
        with patch.dict(os.environ,{'SSH_CONNECTION':'synthetic'}),patch('agent_utils.agterm.remote.ssh_menu') as attach:
            main([])
            attach.assert_called_once()

    def test_terminal_dry_run_and_unsupported_group_do_not_attach(self):
        with patch('subprocess.call') as attach:
            main(['terminal','--dry-run'])
            with self.assertRaises(ValueError):main(['terminal','--companions'])
            attach.assert_not_called()

    def test_agent_filter_matches_executable_names_not_short_substrings(self):
        args=Namespace(window=None,project=None,attention=False,agents_only=True,attention_first=False)
        rows=[{'id':str(i),'panes':[{'foreground':name}]} for i,name in enumerate([
            '/usr/local/bin/pi --resume','cursor-agent','agy','pipeline','spin','company','bash'])]
        self.assertEqual([r['id'] for r in filter_sessions(rows,args)],['0','1','2'])

    def test_ssh_launch_attaches_only_returned_terminal(self):
        from agent_utils.agterm.remote import ssh_menu
        args=Namespace(config='synthetic.json',window=None,project=None,dry_run=False,session=None)
        window={'name':'Demo'};project={'name':'Example','id':'example'}
        with patch('agent_utils.cli.pick',side_effect=['new','Demo',project]), patch('agent_utils.cli.load'), patch('agent_utils.cli.projects',return_value=[(window,project)]), patch('agent_utils.cli.main',return_value={'terminal_id':'new-exact'}) as launch, patch('agent_utils.agterm.remote.terminal_attach') as attach:
            ssh_menu(object(),args)
            launch.assert_called_once_with(['--config','synthetic.json','menu','--window','Demo','--project','example'])
            self.assertEqual(attach.call_args.args[1].session,'new-exact')
            self.assertIsNone(args.session)

    def test_ssh_cancelled_launch_does_not_attach(self):
        from agent_utils.agterm.remote import ssh_menu
        args=Namespace(config='synthetic.json',window=None,project=None,dry_run=False)
        project={'name':'Example','id':'example'}
        with patch('agent_utils.cli.pick',side_effect=['new','Demo',project]), patch('agent_utils.cli.load'), patch('agent_utils.cli.projects',return_value=[({'name':'Demo'},project)]), patch('agent_utils.cli.main',return_value=None), patch('agent_utils.agterm.remote.terminal_attach') as attach:
            ssh_menu(object(),args)
            attach.assert_not_called()

    def test_ssh_zellij_uses_same_config(self):
        from agent_utils.agterm.remote import ssh_menu
        with patch('agent_utils.cli.pick',return_value='zellij'), patch('agent_utils.core.zellij.command',return_value=0) as command:
            ssh_menu(object(),Namespace(config='synthetic.json',dry_run=False))
            command.assert_called_once_with(['menu','--config','synthetic.json'])
