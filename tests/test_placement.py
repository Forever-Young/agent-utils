import os
import tempfile
import unittest
from unittest.mock import Mock,patch
from agent_utils.agterm.placement import place_once,remember_existing
from agent_utils.cli import Agterm,validate
from agent_utils.agterm.state import locked_registry


class PlacementTests(unittest.TestCase):
    def test_new_session_moves_once_but_not_on_restore(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}):
            ag=Mock(dry=False)
            place_once(ag,'window','row','top');place_once(ag,'window','row','top')
            ag.call.assert_called_once_with('session','move','--to','top','--target','row','--window','window')
    def test_bootstrap_baselines_existing_rows(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}):
            remember_existing({'workspaces':[{'sessions':[{'id':'existing'}]}]})
            ag=Mock(dry=False)
            place_once(ag,'window','existing','top');ag.call.assert_not_called()
            place_once(ag,'window','new','top');self.assertEqual(ag.call.call_count,1)
    def test_default_and_dry_run_do_not_write_state(self):
        with patch('agent_utils.agterm.placement.locked_registry',side_effect=AssertionError('write')):
            ag=Mock(dry=True)
            place_once(ag,'window','row','default');ag.call.assert_not_called()
            place_once(ag,'window','row','top');self.assertEqual(ag.call.call_count,1)
    def test_failure_keeps_row_unmarked_for_retry(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_STATE_HOME':d,'XDG_CONFIG_HOME':d}):
            ag=Mock(dry=False);ag.call.side_effect=OSError('socket')
            place_once(ag,'window','row','top')
            with locked_registry('placement') as registry:self.assertNotIn('row',registry.get('placement_seen',[]))
            ag.call.side_effect=None;place_once(ag,'window','row','top')
            self.assertEqual(ag.call.call_count,2)
    def test_launch_moves_exact_returned_row(self):
        with patch.object(Agterm,'window',return_value='wid'),patch.object(Agterm,'call',side_effect=['new-row','ok']) as call,patch('agent_utils.agterm.placement.locked_registry') as registry:
            registry.return_value.__enter__.return_value={}
            ag=Agterm(session_position='top')
            self.assertEqual(ag.launch({'name':'Team'},{'name':'Demo'},['claude'],'/tmp'),'new-row')
            self.assertEqual(call.call_args.args,('session','move','--to','top','--target','new-row','--window','wid'))
    def test_invalid_setting_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'new_session_position'):
            validate({'version':1,'windows':[],'new_session_position':'sideways'})
    def test_startup_applies_setting_to_its_own_row(self):
        import json
        from pathlib import Path
        from agent_utils.agterm.startup import startup
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'config.json'
            path.write_text(json.dumps({'version':1,'new_session_position':'top','windows':[{'name':'Team','default_agent':'claude','projects':[{'id':'p','name':'Demo','roots':[d]}]}]}))
            tree={'workspaces':[{'id':'ws','name':'Demo','sessions':[{'id':'own-row'}]}]}
            env={'AGTERM_ENABLED':'1','AGTERM_WINDOW_ID':'wid','AGTERM_SESSION_ID':'own-row','AGTERM_WORKSPACE_ID':'ws'}
            with patch.dict(os.environ,env),patch('agent_utils.cli.Agterm') as adapter,patch('agent_utils.agterm.placement.place_once') as move,patch('agent_utils.cli.pick',return_value=None),patch('agent_utils.agterm.startup.plain_shell'),patch('os.chdir'):
                ag=adapter.return_value;ag.call.side_effect=[tree,[{'id':'wid','name':'Team'}]]
                startup(path)
                move.assert_called_once_with(ag,'wid','own-row','top')
