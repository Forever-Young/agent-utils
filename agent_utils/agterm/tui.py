"""Optional Textual selector. Imported only when a terminal and Textual exist."""
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Footer, Input, Label, OptionList, Static
from textual.widgets.option_list import Option
from ..cli import SEP


class Selector(App):
    CSS='''
    Screen { padding: 1 2; }
    #heading { text-style: bold; color: $accent; height: 2; }
    Input { margin-bottom: 1; }
    OptionList { height: 1fr; border: round $accent; }
    #detail { height: 5; padding: 1; color: $text-muted; }
    '''
    BINDINGS=[('escape','cancel','Cancel'),('down','next','Next'),('up','previous','Previous')]

    def __init__(self,prompt,entries):
        super().__init__();self.prompt=prompt;self.entries=entries

    def compose(self) -> ComposeResult:
        yield Label(Text(self.prompt),id='heading')
        yield Input(placeholder='Type to filter · Enter selects · Esc cancels',id='filter')
        yield OptionList(id='options')
        hint=('New conversations use the configured project/window provider. Coordinator is an explicit action.'
              if any(value=='coordinator' for _,value in self.entries)
              else 'Type to filter the list. Use arrow keys and Enter to choose; Escape returns without selecting.')
        yield Static(hint,id='detail')
        yield Footer()

    def on_mount(self):
        self.refilter('');self.query_one(Input).focus()

    def refilter(self,value):
        widget=self.query_one(OptionList)
        widget.clear_options()
        terms=value.casefold().split()
        for i,(label,val) in enumerate(self.entries):
            if val is SEP:
                if not terms and widget.option_count:widget.add_option(Option(Text(label,style='dim'),id=str(i),disabled=True))
                continue
            if all(term in label.casefold() for term in terms):widget.add_option(Option(Text(label),id=str(i)))
        first=next((n for n in range(widget.option_count) if not widget.get_option_at_index(n).disabled),None)
        widget.highlighted=first

    def on_input_changed(self,event: Input.Changed):self.refilter(event.value)
    def on_input_submitted(self,event: Input.Submitted):self.choose()
    def on_option_list_option_selected(self,event: OptionList.OptionSelected):
        self.exit(self.entries[int(event.option.id)][1])
    def choose(self):
        widget=self.query_one(OptionList)
        if widget.highlighted is not None and not widget.get_option_at_index(widget.highlighted).disabled:
            self.exit(self.entries[int(widget.get_option_at_index(widget.highlighted).id)][1])
    def action_next(self):self.query_one(OptionList).action_cursor_down()
    def action_previous(self):self.query_one(OptionList).action_cursor_up()
    def action_cancel(self):self.exit(None)


def select(prompt,entries):return Selector(prompt,entries).run()

