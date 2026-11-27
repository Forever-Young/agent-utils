from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agent_utils.core.setup import optional as mod


class Optional(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.home = Path(self.tmp.name)
        self.runner = mock.Mock(return_value=mock.Mock(returncode=0))

    def run_main(self, *args):
        with mock.patch.object(mod.shutil, 'which', return_value='/x/tool'):
            return mod.main(list(args), runner=self.runner, home=self.home)

    def scripts(self):
        return [c[0][0][2].rsplit('.', 1)[1] for c in self.runner.call_args_list]

    def test_list_runs_nothing(self):
        self.assertEqual(self.run_main(), 0); self.runner.assert_not_called()

    def test_detected_applies_kiro_hooks_and_adopt_job_but_not_relay(self):
        self.run_main('--detected', '--apply')
        self.assertEqual(self.scripts(), ['kiro', 'adopt_job'])
        self.assertTrue(all('--apply' in c[0][0] for c in self.runner.call_args_list))

    def test_relay_runs_when_hooks_exist(self):
        hooks = self.home/'.kiro/hooks/agterm-utils-attention.json'
        hooks.parent.mkdir(parents=True); hooks.write_text('{}')
        self.run_main('inbox-relay', '--apply')
        self.assertEqual(self.scripts(), ['relay'])

    def test_without_apply_previews(self):
        self.run_main('kiro-hooks')
        self.assertNotIn('--apply', self.runner.call_args[0][0])

    def test_unknown_name_fails(self):
        with self.assertRaises(SystemExit): self.run_main('nope')


if __name__ == '__main__':
    unittest.main()
