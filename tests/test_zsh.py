import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

@unittest.skipUnless(shutil.which('zsh'),'zsh required')
class ShellTests(unittest.TestCase):
    def test_hook_once_and_nonagterm_skip(self):
        hook=Path(__file__).resolve().parents[1]/'agent_utils/core/setup/agterm-startup.zsh'
        with tempfile.TemporaryDirectory() as d:
            launcher=Path(d)/'launcher';launcher.write_text('#!/bin/sh\necho MENU\n');launcher.chmod(0o755)
            env=dict(os.environ,AGTERM_ENABLED='1',AGTERM_SESSION_ID='test',AGTERM_UTILS_LAUNCHER=str(launcher))
            for name in ['ZELLIJ','AGTERM_UTILS_MENU_SHOWN','AGTERM_UTILS_PLAIN_SHELL']:env.pop(name,None)
            script='source "$1"; _agterm_utils_first_prompt; print -- "hooks:${precmd_functions[*]}"; source "$1"; print -- "after:${precmd_functions[*]}"'
            out=subprocess.check_output(['zsh','-fic',script,'test',str(hook)],env=env,text=True)
            self.assertEqual(out.count('MENU'),1)
            self.assertNotIn('_agterm_utils_first_prompt',out)
            env['AGTERM_ENABLED']='0'
            out=subprocess.check_output(['zsh','-fic','source "$1"; print -- "${precmd_functions[*]}"','test',str(hook)],env=env,text=True)
            self.assertNotIn('_agterm_utils_first_prompt',out)

    def test_coordinator_handoff_exits_only_on_success_signal(self):
        hook=Path(__file__).resolve().parents[1]/'agent_utils/core/setup/agterm-startup.zsh'
        with tempfile.TemporaryDirectory() as d:
            launcher=Path(d)/'launcher'
            launcher.write_text('#!/bin/sh\n[ "$AGTERM_UTILS_STARTUP_HANDOFF" = 1 ] || exit 99\nexit "$TEST_LAUNCH_RESULT"\n')
            launcher.chmod(0o755)
            env=dict(os.environ,AGTERM_ENABLED='1',AGTERM_SESSION_ID='test',AGTERM_UTILS_LAUNCHER=str(launcher))
            for name in ['ZELLIJ','AGTERM_UTILS_MENU_SHOWN','AGTERM_UTILS_PLAIN_SHELL']:
                env.pop(name,None)
            for result in (0,1,10):
                with self.subTest(result=result):
                    env['TEST_LAUNCH_RESULT']=str(result)
                    out=subprocess.check_output(['zsh','-fic','source "$1"; _agterm_utils_first_prompt; print SHELL_REMAINS','test',str(hook)],env=env,text=True)
                    self.assertEqual('SHELL_REMAINS' in out,result!=10)
