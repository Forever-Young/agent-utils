import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_utils.core.agents import AGENTS, AGENT_NAMES, agent_executable
from agent_utils.core.files import atomic_write
from agent_utils.core.paths import config_path, state_dir
from agent_utils import cli


class CoreTests(unittest.TestCase):
    def test_registry_is_shared(self):
        self.assertIs(cli.AGENT_NAMES,AGENT_NAMES);self.assertEqual(AGENTS,tuple(AGENT_NAMES))
        self.assertIn('claude',AGENTS)
        with patch('shutil.which',return_value=None),patch('agent_utils.core.agents.Path.home',return_value=Path('/nonexistent')):
            self.assertIsNone(agent_executable('claude'))

    def test_legacy_directories_are_copied_once_and_kept(self):
        with tempfile.TemporaryDirectory() as d:
            old=Path(d)/'agterm-utils';old.mkdir();(old/'config.json').write_text('{"version": 1}')
            with patch.dict(os.environ,{'XDG_CONFIG_HOME':d,'XDG_STATE_HOME':d}):
                self.assertEqual(config_path(),Path(d)/'agent-utils/config.json')
                self.assertEqual(config_path().read_text(),'{"version": 1}')
                self.assertEqual((old/'config.json').read_text(),'{"version": 1}')
                config_path().write_text('changed');(old/'config.json').write_text('newer old')
                self.assertEqual(config_path().read_text(),'changed')
                self.assertEqual(state_dir(),Path(d)/'agent-utils')

    def test_fresh_install_creates_nothing(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'XDG_CONFIG_HOME':d}):
            self.assertFalse(config_path().parent.exists())

    def test_atomic_write(self):
        with tempfile.TemporaryDirectory() as d:
            atomic_write(Path(d)/'x/y.json',{'a':1})
            self.assertEqual((Path(d)/'x/y.json').read_text(),'{\n  "a": 1\n}\n')


if __name__=='__main__':unittest.main()
