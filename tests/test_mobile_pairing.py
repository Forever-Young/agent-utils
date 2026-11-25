import base64
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from agent_utils.agterm import mobile_pairing as pairing

class MobilePairingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.keyfile = self.root / 'key'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.keyfile)], check=True)
        self.key = pairing.public_key(self.keyfile.with_suffix('.pub').read_text())
    def test_pair_link_contains_pin_and_no_private_material(self):
        link, fingerprint = pairing.pairing('demo.tailnet', 2222, 'demo', str(self.keyfile.with_suffix('.pub')))
        self.assertTrue(link.startswith('agterm-mobile://pair?'))
        self.assertIn('host=demo.tailnet', link); self.assertTrue(fingerprint.startswith('SHA256:'))
        self.assertNotIn('PRIVATE', link)
    def test_authorize_preview_then_atomic_restricted_entry(self):
        target = self.root / 'ssh/authorized_keys'
        with redirect_stdout(io.StringIO()):
            self.assertFalse(pairing.authorize(self.key, path=target))
            self.assertFalse(target.exists())
            self.assertTrue(pairing.authorize(self.key, yes=True, path=target))
            self.assertFalse(pairing.authorize(self.key, yes=True, restricted=False, path=target))
        text = target.read_text()
        self.assertIn('restrict,pty,command="', text)
        self.assertIn(' mobile gate" ', text)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
    def test_bad_keys_and_symlinks_refused(self):
        with self.assertRaises(ValueError): pairing.public_key('command="evil" ' + self.key)
        with self.assertRaises(ValueError): pairing.public_key('ssh-ed25519 aGVsbG8=')
        target = self.root / 'link'; target.symlink_to(self.keyfile)
        with self.assertRaises(ValueError): pairing.authorize(self.key, yes=True, path=target)
    def test_gate_allowlist(self):
        self.assertEqual(pairing.gate_arguments("'agents' 'mobile' 'serve'"), ['serve'])
        self.assertEqual(pairing.gate_arguments('agents mobile attach --session demo --pane right'), ['attach', '--session', 'demo', '--pane', 'right'])
        for command in ['', 'bash', 'agents mobile serve; id', 'agents mobile serve --config evil', 'agents mobile attach --session -x', 'agents mobile attach --session demo --pane other', 'agents mobile authorize --key key']:
            with self.subTest(command=command), self.assertRaises(ValueError): pairing.gate_arguments(command)
    def test_appearance_uses_bundled_theme(self):
        class Fake:
            def call(self, *a, **k): return {'theme': '../../etc/passwd'}
        result = pairing.appearance(Fake())
        self.assertEqual(result['colors']['background'], '#303030')
