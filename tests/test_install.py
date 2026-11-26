from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InstallTests(unittest.TestCase):
    def test_update_and_rollback_preserve_config(self):
        script=Path(__file__).resolve().parents[1]/'scripts/install.py'
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'bin'
            command=[sys.executable,str(script),'--bin-dir',str(dest),'--apply']
            subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
            marker=dest/'agterm-tools/agent_utils/marker.txt';marker.write_text('previous')
            subprocess.run(command,check=True,stdout=subprocess.DEVNULL)
            self.assertFalse(marker.exists())
            subprocess.run(command+['--rollback'],check=True,stdout=subprocess.DEVNULL)
            self.assertEqual(marker.read_text(),'previous')
            result=subprocess.run([str(dest/'agents'),'--help'],check=True,capture_output=True,text=True)
            self.assertIn('agterm',result.stdout);self.assertIn('admin',result.stdout)
            self.assertFalse((dest/'project-session').exists())

    def test_upgrade_from_pre_rename_layout_keeps_kiro_hook_path(self):
        script=Path(__file__).resolve().parents[1]/'scripts/install.py'
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'bin';legacy=dest/'agterm-tools/agterm_utils';legacy.mkdir(parents=True)
            (legacy/'old.py').write_text('old');(dest/'project-session').write_text('old');(dest/'agents').write_text('old')
            subprocess.run([sys.executable,str(script),'--bin-dir',str(dest),'--apply'],check=True,stdout=subprocess.DEVNULL)
            self.assertFalse((legacy/'old.py').exists());self.assertTrue((legacy/'kiro_hook.py').exists())
            run=subprocess.run([sys.executable,str(legacy/'kiro_hook.py'),'attention'],input='{}',text=True,capture_output=True)
            self.assertEqual((run.returncode,run.stdout,run.stderr),(0,'',''))
            subprocess.run([sys.executable,str(script),'--bin-dir',str(dest),'--apply','--rollback'],check=True,stdout=subprocess.DEVNULL)
            self.assertEqual((legacy/'old.py').read_text(),'old')


class BrewTests(unittest.TestCase):
    def test_brew_formula_template_renders_and_matches_pyproject_pins(self):
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / 'scripts'))
        import importlib.util
        spec = importlib.util.spec_from_file_location('brew_local', root / 'scripts/brew-local.py')
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        template = (root / 'packaging/homebrew/agent-utils.rb').read_text()
        out = module.render(template, 'file:///x.tar.gz', '0.1.0-abc', 'f' * 64)
        for token in ('@URL@', '@VERSION@', '@SHA256@'):
            self.assertNotIn(token, out)
        self.assertIn('url "file:///x.tar.gz"', out)
        import re
        pin = re.search(r'textual==([\d.]+)', (root / 'pyproject.toml').read_text()).group(1)
        self.assertIn('textual-%s.tar.gz' % pin, template)

    def test_release_formula_points_at_the_uploaded_asset(self):
        root = Path(__file__).resolve().parents[1]
        import importlib.util
        spec = importlib.util.spec_from_file_location('release', root / 'scripts/release.py')
        module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        text = (root / 'packaging/homebrew/agent-utils-tap.rb').read_text()
        url = module.asset_url('0.2.0')
        out = module.render(module.render(text, url, '0.2.0', 'a' * 64), url, '0.2.0', 'b' * 64)
        self.assertIn('  url "%s"\n' % url, out)
        self.assertEqual(out.count('\n  sha256 "'), 1)
        self.assertIn('  sha256 "%s"\n' % ('b' * 64), out)
        self.assertNotIn('revision:', out)
        self.assertEqual(module.version_of(root), '0.1.1')

    def test_installed_copy_installs_from_recorded_source(self):
        from agent_utils.core.setup import installer
        import json
        with tempfile.TemporaryDirectory() as d:
            helpers = Path(d)/'agterm-tools'; (helpers/'agent_utils').mkdir(parents=True)
            repo = Path(d)/'repo'; (repo/'agent_utils').mkdir(parents=True)
            with self.assertRaises(SystemExit):  # running from the installed copy, nothing recorded
                installer.find_source(helpers, helpers)
            (helpers/'install-manifest.json').write_text(json.dumps({'source': str(repo)}))
            self.assertEqual(installer.find_source(helpers, helpers), repo)
            self.assertEqual(installer.find_source(helpers, repo), repo.resolve())

    def test_paths_prefers_installed_copy(self):
        root = Path(__file__).resolve().parents[1]
        from agent_utils.core.setup.paths import package_dir
        with tempfile.TemporaryDirectory() as d:
            installed = Path(d) / '.bin/agterm-tools/agent_utils';installed.mkdir(parents=True)
            self.assertEqual(package_dir(d), installed)
            self.assertTrue(package_dir(Path(d) / 'empty').name == 'agent_utils')
