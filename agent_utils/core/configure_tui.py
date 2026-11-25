"""Full-screen Textual app for `agents configure`: the same settings, entries and actions as
the list menu in settings.py, with a section list, an entry list and a log pane.
Every change shows its diff and asks before writing; actions stream their output into the log.
Imported only when Textual exists."""
import subprocess
import threading

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from . import configure_flows as flows
from . import settings


class Ask(ModalScreen):
    """One question: text input (kind 'input'), a list (kind 'choice') or yes/no with a body (kind 'confirm')."""
    BINDINGS = [('escape', 'cancel', 'Cancel')]
    CSS = '''
    Ask { align: center middle; }
    #box { width: 90%; max-width: 110; height: auto; max-height: 90%; border: round $accent; background: $surface; padding: 1 2; }
    #title { text-style: bold; color: $accent; margin-bottom: 1; }
    #body { max-height: 24; margin-bottom: 1; }
    #hint { color: $text-muted; }
    '''

    def __init__(self, kind, title, body='', initial='', choices=()):
        super().__init__()
        self.kind, self.title_text, self.body_text, self.initial, self.choices = kind, title, body, initial, list(choices)

    def compose(self) -> ComposeResult:
        with Vertical(id='box'):
            yield Label(Text(self.title_text), id='title')
            if self.body_text:
                with VerticalScroll(id='body'):
                    yield Static(Text(self.body_text))
            if self.kind == 'input':
                yield Input(value=self.initial, id='field')
                yield Label('Enter confirms, Esc cancels. An empty value changes nothing.', id='hint')
            elif self.kind == 'choice':
                yield OptionList(*[Option(Text(label), id=str(i)) for i, (label, _) in enumerate(self.choices)], id='choices')
                yield Label('Enter picks, Esc cancels.', id='hint')
            else:
                yield Label('y or Enter applies, n or Esc cancels.', id='hint')

    def on_mount(self):
        if self.kind == 'input':
            self.query_one(Input).focus()
        elif self.kind == 'choice':
            self.query_one(OptionList).focus()

    def on_key(self, event):
        if self.kind == 'confirm':
            if event.key in ('y', 'enter'):
                event.stop(); self.dismiss(True)
            elif event.key == 'n':
                event.stop(); self.dismiss(False)

    def on_input_submitted(self, event):
        event.stop(); self.dismiss(event.value)

    def on_option_list_option_selected(self, event):
        event.stop(); self.dismiss(self.choices[int(event.option.id)][1])

    def action_cancel(self):
        self.dismiss(None)


class ConfigureApp(App):
    TITLE = 'agents configure'
    CSS = '''
    #sections { width: 34; border: round $accent; }
    #right { width: 1fr; }
    #entries { height: 1fr; border: round $accent; }
    #log { height: 12; border: round $primary; }
    #help { height: 3; padding: 0 1; color: $text-muted; }
    '''
    BINDINGS = [('q', 'quit', 'Quit'), ('tab', 'switch', 'Switch pane'), ('r', 'reload', 'Reload'),
                ('u', 'undo', 'Undo a file'), ('escape', 'back', 'Back to sections')]

    def __init__(self):
        super().__init__()
        self.section = settings.SECTIONS[0]
        self.items = []
        self.busy = False
        self.last_output = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield OptionList(*[Option(Text(settings.SECTION_TITLES.get(s, s)), id=s) for s in settings.SECTIONS], id='sections')
            with Vertical(id='right'):
                yield OptionList(id='entries')
                yield Static('', id='help')
                yield RichLog(id='log', wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self):
        self.query_one('#sections', OptionList).highlighted = 0
        self.load_entries()
        self.query_one('#sections').focus()
        self.say('Enter opens a section or changes a setting. Every change shows its diff first. q quits.')

    # ---- helpers
    def say(self, text):
        self.query_one('#log', RichLog).write(Text(str(text)))

    def load_entries(self):
        try:
            entries = flows.entries(self.section)
        except ValueError as exc:
            self.say('Cannot read settings: %s' % exc)
            entries = []
        self.items = entries
        widget = self.query_one('#entries', OptionList)
        widget.clear_options()
        for i, (label, _) in enumerate(entries):
            widget.add_option(Option(Text(label), id=str(i)))
        if entries:
            widget.highlighted = 0
        self.query_one('#help', Static).update(Text(settings.SECTION_TITLES.get(self.section, self.section)))

    async def ask(self, kind, title, **kw):
        return await self.push_screen_wait(Ask(kind, title, **kw))

    def action_switch(self):
        sections, entries = self.query_one('#sections'), self.query_one('#entries')
        (entries if sections.has_focus else sections).focus()

    def action_back(self):
        self.query_one('#sections').focus()

    def action_reload(self):
        self.load_entries(); self.say('Reloaded.')

    # ---- section and entry selection
    def on_option_list_option_highlighted(self, event):
        if event.option_list.id == 'sections' and event.option.id != self.section:
            self.section = event.option.id
            self.load_entries()

    def on_option_list_option_selected(self, event):
        if event.option_list.id == 'sections':
            self.query_one('#entries').focus()
            return
        if event.option_list.id != 'entries' or self.busy:
            return
        self.busy = True
        self.run_worker(self.handle(*self.items[int(event.option.id)][1]), exclusive=True)

    async def handle(self, kind, item):
        try:
            if kind == 'info':
                self.say(item)
                return
            gen = item()
            try:
                step = next(gen)
                while True:
                    t = step[0]
                    if t == 'choice':
                        answer = await self.ask('choice', step[1], choices=step[2], body=step[3] if len(step) > 3 else '')
                        if answer is None:
                            gen.close(); return
                    elif t == 'text':
                        answer = await self.ask('input', step[1].split('\n')[0], body='\n'.join(step[1].split('\n')[1:]), initial=step[2])
                        if answer is None or not answer.strip():
                            gen.close(); return
                    elif t == 'confirm':
                        body = step[2] if step[2] is not None else '\n'.join(self.last_output) or 'Nothing was printed.'
                        answer = bool(await self.ask('confirm', step[1], body=body))
                    elif t == 'run':
                        answer = await self.stream(step[1], step[2], step[3])
                    else:
                        self.say(step[1]); answer = None
                    step = gen.send(answer)
            except StopIteration:
                pass
        except ValueError as exc:
            self.say('Error: %s' % exc)
        finally:
            self.busy = False
            self.load_entries()

    async def stream(self, item, arg, apply):
        """Run the child process; its output goes to the log as it arrives."""
        import asyncio
        argv = item.command(arg, apply)
        self.say('$ ' + ' '.join(['agents'] + argv[3:] if argv[1:3] == ['-m', 'agent_utils.entry'] else argv[1:]))
        code = 0
        self.last_output = []
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()

        def work():
            proc = subprocess.Popen(argv, env=settings.child_env(), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
            for line in proc.stdout:
                loop.call_soon_threadsafe(queue.put_nowait, line.rstrip('\n'))
            loop.call_soon_threadsafe(queue.put_nowait, proc.wait())
        threading.Thread(target=work, daemon=True).start()
        while True:
            line = await queue.get()
            if isinstance(line, int):
                if line:
                    self.say('exit %d' % line)
                return line
            self.last_output.append(line)
            self.say(line)

    def action_undo(self):
        if not self.busy:
            self.busy = True
            self.run_worker(self.undo_flow(), exclusive=True)

    async def undo_flow(self):
        try:
            file_id = await self.ask('choice', 'Undo the last change in which file?', choices=[(f, f) for f in sorted(settings.FILES)])
            if file_id is None:
                return
            lines = []
            try:
                settings.undo(file_id, False, lines.append)
            except ValueError as exc:
                self.say('Error: %s' % exc)
                return
            if await self.ask('confirm', 'Restore the newest backup of %s?' % file_id, body='\n'.join(lines[:-1] if len(lines) > 1 else lines)):
                settings.undo(file_id, True, self.say)
        finally:
            self.busy = False
            self.load_entries()


def run_app():
    ConfigureApp().run()
    return 0
