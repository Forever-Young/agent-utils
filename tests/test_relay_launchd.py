from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock

from agent_utils.core.setup import relay as mod


class RelayLaunchd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.home = Path(self.tmp.name)
        self.plist = self.home/'Library/LaunchAgents/agent-utils.inbox-relay.plist'

    def run_main(self, *args):
        runner = mock.Mock(return_value=mock.Mock(returncode=0, stderr=''))
        code = mod.main(['--home', str(self.home), *args], runner=runner)
        return code, runner

    def test_preview_writes_nothing(self):
        code, runner = self.run_main()
        self.assertEqual(code, 0); self.assertFalse(self.plist.exists()); runner.assert_not_called()

    def test_apply_writes_and_loads(self):
        code, runner = self.run_main('--apply')
        data = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(code, 0)
        self.assertEqual(data['ProgramArguments'][1:], ['admin', 'inbox', 'relay'])
        self.assertTrue(data['KeepAlive']); self.assertTrue(data['RunAtLoad'])
        self.assertEqual(runner.call_args[0][0][:2], ['launchctl', 'bootstrap'])

    def test_remove_deletes(self):
        self.run_main('--apply'); code, runner = self.run_main('--remove', '--apply')
        self.assertFalse(self.plist.exists()); self.assertEqual(runner.call_args[0][0][:2], ['launchctl', 'bootout'])


if __name__ == '__main__':
    unittest.main()
