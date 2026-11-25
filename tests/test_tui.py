import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('textual'),'optional Textual not installed')
class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_filter_selects_original_value(self):
        from agent_utils.agterm.tui import Selector
        from textual.widgets import Input,OptionList
        app=Selector('Synthetic project',[('New claude',('claude',False)),('New codex',('codex',False)),('Coordinator','coordinator')])
        async with app.run_test() as pilot:
            self.assertEqual(app.query_one(OptionList).highlighted,0)
            await pilot.press('c','o','d','e','x')
            await pilot.pause()
            self.assertEqual(app.query_one(OptionList).option_count,1)
            await pilot.press('enter')
        self.assertEqual(app.return_value,('codex',False))
    async def test_down_and_escape(self):
        from agent_utils.agterm.tui import Selector
        app=Selector('Actions',[('First',1),('Second',2)])
        async with app.run_test() as pilot:
            await pilot.press('down','enter')
        self.assertEqual(app.return_value,2)
        app=Selector('Actions',[('First',1)])
        async with app.run_test() as pilot:await pilot.press('escape')
        self.assertIsNone(app.return_value)

    async def test_separator_is_not_selectable(self):
        from agent_utils.cli import SEPARATOR
        from agent_utils.agterm.tui import Selector
        from textual.widgets import OptionList
        app=Selector('Menu',[('First',1),SEPARATOR,('Second',2)])
        async with app.run_test() as pilot:
            widget=app.query_one(OptionList)
            await pilot.press('down')
            await pilot.pause()
            self.assertEqual(widget.get_option_at_index(widget.highlighted).id,'2')
            await pilot.press('up','enter')
        self.assertEqual(app.return_value,1)
