import os
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from agent_utils.agterm import cheatsheet

SAMPLE = """keymap: {conf}

actions:
    new_session                 cmd+n
    increase_font_size          cmd++
    rename_session              -
  * clear_status                ctrl+shift+c
    toggle_scratch              cmd+j
    next_attention_session      ctrl+opt+down
    brand_new_action            cmd+shift+9

menu:
    cmd+n  File ▸ New Session
    cmd+shift+y  Agterm ▸ Make New Sticky Note
    cmd+h  Agterm ▸ Hide Agterm
    cmd+shift+r  File ▸ Attach Remote…  (disabled)
    fn+d  Edit ▸ Start Dictation…
    ctrl+opt+down  Window ▸ Bottom
    cmd+j  View ▸ Show Scratch  (disabled)
"""


class CheatsheetTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.conf = Path(self.dir.name) / 'keymap.conf'

    def model(self, conf_text=''):
        self.conf.write_text(conf_text)
        return cheatsheet.build(SAMPLE.format(conf=self.conf))

    def test_parse_list(self):
        path, actions, menu = cheatsheet.parse_list(SAMPLE.format(conf='/x'))
        self.assertEqual(path, '/x')
        self.assertIn(('rename_session', None), actions)
        self.assertIn(('clear_status', 'ctrl+shift+c'), actions)
        self.assertIn(('increase_font_size', 'cmd++'), actions)
        self.assertIn(('cmd+shift+r', 'File ▸ Attach Remote…', True), menu)

    def test_chords(self):
        self.assertEqual(cheatsheet.split_chord('cmd++'), (['cmd'], '+'))
        self.assertEqual(cheatsheet.pretty_chord('cmd+shift+return'), '⌘⇧↩')
        self.assertEqual(cheatsheet.pretty_chord('cmd+t|ctrl+a>t', symbols=False), 'Cmd+T or Ctrl+A then T')

    def test_model(self):
        model = self.model()
        flat = {d: (k, n) for _, rows in model['sections'] for k, d, n in rows}
        self.assertEqual(flat['Increase font size'][0], '⌘+')
        self.assertEqual(flat['Scratch shell over the session; press again to hide'], ('⌘J', 'disabled now'))
        self.assertEqual(flat['brand_new_action'][0], '⌘⇧9')  # unknown action keeps its raw name
        text = cheatsheet.render_md(model)
        self.assertNotIn('Sticky Note', text)
        self.assertNotIn('Dictation', text)
        self.assertIn('Hide agterm', text)
        self.assertEqual([a for a, _ in model['unbound']], ['rename_session'])
        self.assertFalse(model['overrides'])
        self.assertEqual(len(model['conflicts']), 1)
        self.assertIn('Window ▸ Bottom', model['conflicts'][0][1])

    def test_conf_overrides_and_custom_commands(self):
        model = self.model('# c\nmap cmd+t|ctrl+a>t toggle_scratch\ncommand "Open notes" ctrl+a>g open notes\ncommand "Palette only" echo hi\n')
        self.assertTrue(model['overrides'])
        rows = dict((d, k) for _, r in model['sections'] for k, d, n in r)
        self.assertEqual(rows['Scratch shell over the session; press again to hide'], '⌘T or ⌃A then T')
        self.assertEqual(rows['Open notes'], '⌃A then G')
        self.assertEqual(rows['Palette only'], 'palette only')

    def test_html_escapes(self):
        self.assertIn('&lt;chord&gt;', cheatsheet.render_html(self.model()))

    def test_command_dry_run_and_no_overwrite(self):
        out = Path(self.dir.name) / 'sheet.md'
        args = Namespace(format='md', output=str(out), force=False, dry_run=True, open=False)
        with patch.object(cheatsheet, 'run_list') as run:
            cheatsheet.command(args)
            run.assert_not_called()
            self.assertFalse(out.exists())
            args.dry_run = False
            run.return_value = SAMPLE.format(conf=self.conf)
            cheatsheet.command(args)
            self.assertTrue(out.exists())
            with self.assertRaises(ValueError):
                cheatsheet.command(args)
            args.force = True
            cheatsheet.command(args)

    @unittest.skipUnless(cheatsheet.have_reportlab(), 'reportlab not installed')
    def test_pdf(self):
        out = Path(self.dir.name) / 'sheet.pdf'
        cheatsheet.render_pdf(self.model(), out)
        self.assertTrue(out.read_bytes().startswith(b'%PDF'))


if __name__ == '__main__':
    unittest.main()
