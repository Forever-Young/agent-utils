import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

from agent_utils.core import settings
from agent_utils.core.setup import adopt_job as mod


class AdoptJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.home = Path(self.tmp.name)
        self.plist = self.home/'Library/LaunchAgents/agent-utils.inbox-adopt.plist'

    def run_main(self, *args):
        runner = mock.Mock(return_value=mock.Mock(returncode=0, stderr=''))
        return mod.main(['--home', str(self.home), *args], runner=runner), runner

    def set_minutes(self, value):
        path = self.home/'.config/agent-utils/config.json'
        path.parent.mkdir(parents=True); path.write_text(json.dumps({'version': 1, 'windows': [], 'inbox': {'adopt_interval_minutes': value}}))

    def test_preview_writes_nothing(self):
        code, runner = self.run_main()
        self.assertEqual(code, 0); self.assertFalse(self.plist.exists()); runner.assert_not_called()

    def test_apply_runs_on_a_timer_not_a_loop(self):
        code, runner = self.run_main('--apply')
        data = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(data['ProgramArguments'][1:], ['admin', 'inbox', 'adopt', '--auto'])
        self.assertEqual(data['StartInterval'], 300); self.assertNotIn('KeepAlive', data)
        self.assertEqual(runner.call_args[0][0][:2], ['launchctl', 'bootstrap'])

    def test_period_comes_from_config(self):
        self.set_minutes(15); self.run_main('--apply')
        self.assertEqual(plistlib.loads(self.plist.read_bytes())['StartInterval'], 900)

    def test_bad_period_falls_back(self):
        self.set_minutes(0); self.assertEqual(mod.minutes(self.home), 5)

    def test_remove_deletes(self):
        self.run_main('--apply'); self.run_main('--remove', '--apply')
        self.assertFalse(self.plist.exists())


class AdoptSettings(unittest.TestCase):
    def test_int_setting_parses_and_rejects(self):
        item = settings.find('inbox.adopt_interval_minutes')
        self.assertEqual(item.parse('10'), 10)
        for bad in ('0', '1441', 'x', '2.5'):
            with self.assertRaises(ValueError): item.parse(bad)

    def test_auto_adopt_defaults_on(self):
        self.assertTrue(settings.find('inbox.auto_adopt').default)

    def test_config_validation(self):
        from agent_utils.cli import validate
        base = {'version': 1, 'windows': []}
        validate(dict(base, inbox={'auto_adopt': False, 'adopt_interval_minutes': 5}))
        for bad in ({'auto_adopt': 'no'}, {'adopt_interval_minutes': 0}, {'adopt_interval_minutes': True}):
            with self.assertRaises(ValueError): validate(dict(base, inbox=bad))


if __name__ == '__main__':
    unittest.main()
