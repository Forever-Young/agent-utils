"""Hotkeys cheatsheet for agterm: `agents agterm cheatsheet`.

Data: `agtermctl keymap list` (effective bindings), the user's keymap.conf (custom
commands, leader sequences, alternatives) and DESCRIPTIONS below. An action with no
description is still printed under its raw name, so a new agterm version never breaks
the run. The output reflects a personal keymap: it goes to the state directory by
default, never into the repository.
"""
import html
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
from datetime import date

from ..core.paths import state_dir

# Action names were taken from `agtermctl keymap list` on this date.
DESCRIBED_FOR = 'agterm action list checked 2026-11'
MODS = ('ctrl', 'cmd', 'opt', 'shift', 'fn')
SYMBOLS = {'cmd': '⌘', 'shift': '⇧', 'opt': '⌥', 'ctrl': '⌃', 'fn': 'fn'}
KEYS = {'return': '↩', 'left': '←', 'right': '→', 'up': '↑', 'down': '↓', 'space': 'Space',
        'tab': '⇥', 'escape': '⎋', 'delete': '⌫'}

# (section title, [(action, description)])
SECTIONS = [
    ('Sessions, workspaces, windows', [
        ('new_session', "New session (in current session's directory)"), ('new_workspace', 'New workspace'),
        ('new_window', 'New window'), ('open_directory', 'Open directory as a session'),
        ('close_session', 'Close session (also leaves zoom, closes overlay)'),
        ('reopen_recent', 'Reopen last closed item'), ('undo_close', 'Undo close'),
        ('attach_remote', 'Attach remote session (Mac-to-Mac)'),
        ('rename_session', 'Rename session'), ('duplicate_session', 'Duplicate session'),
        ('new_companion', 'New companion terminal'), ('duplicate_companion', 'Duplicate companion'),
        ('convert_companion', 'Convert companion to session'),
        ('rename_workspace', 'Rename workspace'), ('delete_workspace', 'Delete workspace'),
        ('rename_window', 'Rename window'), ('delete_window', 'Delete window'),
        ('previous_window', 'Previous window'), ('next_window', 'Next window'),
        ('open_remote_window', 'Open a remote window'), ('detached_sessions', 'Detached sessions')]),
    ('Navigation', [
        ('previous_session', 'Previous session'), ('next_session', 'Next session'),
        ('previous_attention_session', 'Previous session needing attention'),
        ('next_attention_session', 'Next session needing attention'),
        ('focus_left_pane', 'Focus left pane (when split)'), ('focus_right_pane', 'Focus right pane (when split)'),
        ('session_palette', 'Go to session (fuzzy palette)'), ('command_palette', 'Command palette (all actions)'),
        ('custom_command_palette', 'Custom commands palette'),
        ('show_attention', 'Go to attention (sessions waiting on you)'),
        ('dashboard', 'Dashboard: grid of recent sessions'),
        ('first_session', 'First session'), ('last_session', 'Last session'),
        ('previous_workspace', 'Previous workspace'), ('next_workspace', 'Next workspace'),
        ('focus_workspace', 'Focus a workspace')]),
    ('Splits, scratch, overlays', [
        ('toggle_split', 'Toggle vertical split (second shell beside)'),
        ('toggle_horizontal_split', 'Toggle horizontal split'),
        ('toggle_scratch', 'Scratch shell over the session; press again to hide'),
        ('quick_terminal', 'Quick terminal: floating throwaway shell'),
        ('toggle_overlay', 'Hide / show program and page overlays'),
        ('toggle_terminal_zoom', 'Zoom one terminal full window'),
        ('toggle_search', 'Find in scrollback (Enter next, Shift-Enter prev, Esc close)')]),
    ('View', [
        ('toggle_flag', 'Flag / unflag session (working set across workspaces)'),
        ('clear_status', 'Clear session status'),
        ('toggle_sidebar', 'Show / hide sidebar'), ('toggle_fullscreen', 'Full screen'),
        ('increase_font_size', 'Increase font size'), ('decrease_font_size', 'Decrease font size'),
        ('reset_font_size', 'Reset font size'), ('select_theme', 'Choose a theme'),
        ('toggle_flagged_view', 'Show only flagged sessions'),
        ('toggle_workspace_filter', 'Filter by workspace'), ('toggle_workspace_collapse', 'Collapse the workspace'),
        ('edit_flagged_sessions', 'Edit flagged sessions'), ('edit_focused_workspaces', 'Edit focused workspaces')]),
]
DESCRIPTIONS = {action: text for _, items in SECTIONS for action, text in items}
ACTION_SECTION = {action: title for title, items in SECTIONS for action, _ in items}

# Items agterm's menus gain from macOS (Services, system Edit entries) rather than agterm itself.
SYSTEM_MENU_ITEMS = {'Edit': ('Start Dictation', 'Emoji'), }
OWN_APP_ITEMS = ('Settings', 'Hide', 'Quit')
COLUMNS = (('Sessions, workspaces, windows', 'Navigation'), ('Splits, scratch, overlays', 'View'),
           ('Edit and app', 'Window placement'))
MENU_SKIP = ('View ▸ Enter Full Screen',)  # same as the toggle_fullscreen action under another chord


def menu_section(label):
    menu, _, item = label.partition(' ▸ ')
    if menu == 'Window':
        return 'Edit and app' if item.startswith('Minimize') else 'Window placement'
    if menu in ('Edit', 'Agterm'):
        return 'Edit and app'
    return {'File': 'Sessions, workspaces, windows', 'Navigate': 'Navigation', 'View': 'View'}.get(menu, 'Edit and app')


def menu_text(label):
    menu, _, item = label.partition(' ▸ ')
    return {'Window ▸ Top': 'Top half', 'Window ▸ Bottom': 'Bottom half', 'Window ▸ Fill': 'Fill screen',
            'Agterm ▸ Hide Agterm': 'Hide agterm'}.get(label, item.replace('…', ''))


def run_list():
    result = subprocess.run(['agtermctl', 'keymap', 'list'], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise ValueError('agtermctl keymap list failed: ' + (result.stderr.strip() or 'no output'))
    return result.stdout


def parse_list(text):
    """Return (keymap path, [(action, chord or None)], [(chord, 'Menu ▸ Item', disabled)])."""
    path, actions, menu, section = None, [], [], None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith('keymap:'):
            path = line.split(':', 1)[1].strip()
        elif line in ('actions:', 'menu:'):
            section = line[:-1]
        elif line.endswith(':') and not raw.startswith(' '):
            section = None
        elif section == 'actions':
            line = line[1:].strip() if line.startswith('*') else line  # '*' marks a keymap.conf override
            parts = line.split(None, 1)
            if len(parts) == 2:
                actions.append((parts[0], None if parts[1].strip() == '-' else parts[1].strip()))
        elif section == 'menu':
            disabled = line.endswith('(disabled)')
            if disabled:
                line = line[:-len('(disabled)')].rstrip()
            parts = re.split(r'\s{2,}', line, maxsplit=1)
            if len(parts) == 2:
                menu.append((parts[0], parts[1].strip(), disabled))
    return path, actions, menu


def split_chord(chord):
    """['cmd','shift','return'] for cmd+shift+return. A literal plus key (cmd++) is kept."""
    mods, rest = [], chord
    while True:
        for mod in MODS:
            if rest.startswith(mod + '+') and len(rest) > len(mod) + 1:
                mods.append(mod)
                rest = rest[len(mod) + 1:]
                break
        else:
            return mods, rest


def pretty_chord(chord, symbols=True):
    """Display one binding: alternatives joined by ' or ', leader steps by ' then '."""
    shown = []
    for alt in chord.split('|'):
        steps = []
        for step in re.split(r'(?<=[^+])>(?=.)', alt):
            mods, key = split_chord(step)
            key = KEYS.get(key, key.upper() if len(key) == 1 else key)
            if symbols:
                steps.append(''.join(SYMBOLS[m] if m != 'fn' else 'fn ' for m in mods) + key)
            else:
                steps.append('+'.join([m.capitalize() for m in mods] + [key]))
        shown.append(' then '.join(steps))
    return ' or '.join(shown)


def normalize(chord):
    mods, key = split_chord(chord)
    return '+'.join(sorted(mods) + [key])


def parse_conf(path):
    """Active `map` and `command` lines of keymap.conf: ({action: chords}, [(name, chords)])."""
    maps, commands = {}, []
    try:
        lines = Path(path).expanduser().read_text(errors='replace').splitlines()
    except (OSError, TypeError):
        return maps, commands
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            words = shlex.split(line)
        except ValueError:
            continue
        if words[0] == 'map':
            words = [w for w in words[1:] if w != '--repeat']
            if len(words) >= 2:
                maps[words[1]] = words[0]
        elif words[0] == 'command' and len(words) >= 2:
            rest = [w for w in words[2:] if w != '--repeat']
            chord = rest[0] if rest and re.match(r'^(ctrl|cmd|opt|shift|fn|f\d+)', rest[0]) else None
            commands.append((words[1], chord))
    return maps, commands


def is_system_item(label):
    menu, _, item = label.partition(' ▸ ')
    if menu == 'Agterm':
        return not item.startswith(OWN_APP_ITEMS)
    return item.startswith(SYSTEM_MENU_ITEMS.get(menu, ()))


def build(listing, conf_path=None, symbols=True):
    """Pure model of the sheet from `keymap list` text and an optional keymap.conf path."""
    path, actions, menu = parse_list(listing)
    maps, commands = parse_conf(conf_path or path)
    menu = [m for m in menu if not is_system_item(m[1])]
    disabled = {normalize(c) for c, _, d in menu if d}
    rows, unbound, bound_chords = {}, [], set()
    for action, chord in actions:
        chord = maps.get(action) or chord
        if not chord:
            unbound.append(action)
            continue
        for alt in chord.split('|'):
            if '>' not in alt:
                bound_chords.add(normalize(alt))
        title = ACTION_SECTION.get(action, 'Other actions')
        note = 'disabled now' if normalize(chord.split('|')[0]) in disabled else ''
        rows.setdefault(title, []).append((pretty_chord(chord, symbols), DESCRIPTIONS.get(action, action), note))
    for chord, label, off in menu:
        if normalize(chord) in bound_chords and not label.startswith('Window ▸'):
            continue
        if label in MENU_SKIP:
            continue
        shared = [a for a, c in actions if c and normalize(c.split('|')[0]) == normalize(chord)]
        note = 'disabled now' if off else ''
        if label.startswith('Window ▸') and shared:
            note = 'same keys as ' + DESCRIPTIONS.get(shared[0], shared[0]).lower()
        rows.setdefault(menu_section(label), []).append((pretty_chord(chord, symbols), menu_text(label), note))
    custom = [(pretty_chord(c, symbols) if c else 'palette only', n, '') for n, c in commands]
    if custom:
        rows.setdefault('View', []).extend(custom)
    order = [t for col in COLUMNS for t in col] + ['Other actions']
    sections = [(t, rows[t]) for t in order if t in rows]
    return {'title': 'agterm hotkeys', 'generated': date.today().isoformat(), 'described_for': DESCRIBED_FOR,
            'overrides': bool(maps or commands), 'sections': sections,
            'unbound': [(a, DESCRIPTIONS.get(a, '')) for a in unbound], 'conflicts': conflicts(actions, menu, maps)}


def conflicts(actions, menu, maps=None):
    """Chords used by two different actions or two different menu items."""
    owners = {}
    for action, chord in actions:
        chord = (maps or {}).get(action) or chord
        for alt in (chord or '').split('|'):
            if alt and '>' not in alt:
                owners.setdefault(normalize(alt), set()).add('action ' + action)
    for chord, label, _ in menu:
        owners.setdefault(normalize(chord), set()).add(label)
    # An action and the menu item with the same chord are normally one binding. The Window
    # menu is macOS window placement, which agterm has no actions for, so a shared chord there is a clash.
    result = []
    for chord, names in sorted(owners.items()):
        actions_only = {n for n in names if n.startswith('action ')}
        menus = names - actions_only
        window = {n for n in menus if n.startswith('Window ▸')}
        if len(actions_only) > 1 or len(menus) > 1 or (actions_only and window):
            result.append((pretty_chord(chord), sorted(names)))
    return result


def render_md(model):
    out = ['# ' + model['title'], '',
           '%s. Overrides in keymap.conf: %s.' % (model['generated'], 'yes' if model['overrides'] else 'none'), '']
    for title, rows in model['sections']:
        out += ['## ' + title, '', '| Keys | Action | Note |', '|---|---|---|']
        out += ['| %s | %s | %s |' % (k.replace('|', '\\|'), d, n) for k, d, n in rows] + ['']
    if model['conflicts']:
        out += ['## Conflicts', ''] + ['- %s is used by: %s' % (c, ', '.join(n)) for c, n in model['conflicts']] + ['']
    if model['unbound']:
        out += ['## No shortcut', '', ', '.join(a for a, _ in model['unbound']), '',
                'Bind one in keymap.conf with `map <chord> <action>`, then run `agtermctl keymap reload`.', '']
    out.append('Descriptions: ' + model['described_for'] + '.')
    return '\n'.join(out) + '\n'


def render_html(model):
    e = html.escape
    out = ['<!doctype html><meta charset="utf-8"><title>%s</title>' % e(model['title']),
           '<style>body{font:13px -apple-system,sans-serif;margin:24px}main{columns:3 320px;column-gap:28px}'
           'section{break-inside:avoid;margin-bottom:14px}h2{font-size:14px;margin:0 0 4px;border-bottom:1px solid #999}'
           'td{padding:1px 8px 1px 0;vertical-align:top}td.k{white-space:nowrap;font-weight:600}.n{color:#a33}'
           '@page{size:A4 landscape;margin:12mm}</style>',
           '<h1>%s</h1><p>%s. Overrides in keymap.conf: %s.</p><main>' % (
               e(model['title']), e(model['generated']), 'yes' if model['overrides'] else 'none')]
    for title, rows in model['sections']:
        out.append('<section><h2>%s</h2><table>' % e(title))
        for k, d, n in rows:
            out.append('<tr><td class="k">%s</td><td>%s</td><td class="n">%s</td></tr>' % (e(k), e(d), e(n)))
        out.append('</table></section>')
    out.append('</main>')
    if model['conflicts']:
        out.append('<h2>Conflicts</h2><ul>' + ''.join(
            '<li>%s is used by: %s</li>' % (e(c), e(', '.join(n))) for c, n in model['conflicts']) + '</ul>')
    if model['unbound']:
        out.append('<p><b>No shortcut:</b> %s. Bind one in keymap.conf with <code>map &lt;chord&gt; &lt;action&gt;</code>, '
                   'then run <code>agtermctl keymap reload</code>.</p>' % e(', '.join(a for a, _ in model['unbound'])))
    out.append('<p>Descriptions: %s.</p>' % e(model['described_for']))
    return '\n'.join(out) + '\n'


FONT_PATHS = ('/Library/Fonts/Arial Unicode.ttf', '/System/Library/Fonts/Supplemental/Arial Unicode.ttf')


def pdf_font():
    for path in FONT_PATHS:
        if os.path.exists(path):
            return path
    return None


def have_reportlab():
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


BLUE = '#3a5a9a'
BAND = '#eef1f7'
GREY = '#667088'


def pdf_columns(sections):
    """Fixed three-column arrangement; sections outside COLUMNS go to the last column."""
    by_title = dict(sections)
    cols = [[(t, by_title[t]) for t in col if t in by_title] for col in COLUMNS]
    known = {t for col in COLUMNS for t in col}
    cols[-1] += [(t, r) for t, r in sections if t not in known]
    return cols


def render_pdf(model, path):
    """One landscape A4 page, three columns, blue section bars and banded rows. Needs reportlab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import BaseDocTemplate, Frame, FrameBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle
    font = 'Helvetica'
    font_file = pdf_font()
    if font_file:
        pdfmetrics.registerFont(TTFont('SheetFont', font_file))
        font = 'SheetFont'
    margin, gap = 28, 14
    width, height = landscape(A4)
    col = (width - 2 * margin - 2 * gap) / 3
    top = height - margin - 38
    frames = [Frame(margin + i * (col + gap), margin + 70, col, top - margin - 70, leftPadding=0, rightPadding=0,
                    topPadding=0, bottomPadding=0, id='c%d' % i) for i in range(3)]
    cell = ParagraphStyle('c', fontName=font, fontSize=7.6, leading=9.4)
    key = ParagraphStyle('k', parent=cell)
    bar = ParagraphStyle('b', fontName='Helvetica-Bold', fontSize=9, leading=11, textColor=colors.white)
    note_style = ParagraphStyle('n', fontName=font, fontSize=6.8, leading=8.6, textColor=colors.HexColor(GREY))
    esc = html.escape
    story = []
    for index, column in enumerate(pdf_columns(model['sections'])):
        for title, rows in column:
            data = [[Paragraph(esc(title), bar), '']]
            for k, d, n in rows:
                data.append([Paragraph(esc(k), key), Paragraph(esc(d) + (' <i>(%s)</i>' % esc(n) if n else ''), cell)])
            table = Table(data, colWidths=[col * 0.3, col * 0.7])
            table.setStyle(TableStyle([
                ('SPAN', (0, 0), (1, 0)), ('BACKGROUND', (0, 0), (1, 0), colors.HexColor(BLUE)),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor(BAND)]),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2), ('LEFTPADDING', (0, 0), (-1, -1), 5)]))
            story += [table, Spacer(1, 9)]
        if index < 2:
            story.append(FrameBreak())
    notes = []
    if model['unbound']:
        notes.append('Actions with no shortcut (use %s palette, or bind in keymap.conf): %s.' % (
            pretty_chord('ctrl+shift+p', pdf_font() is not None), esc(', '.join(a for a, _ in model['unbound']))))
    if model['conflicts']:
        notes.append('Shared keys: ' + esc('; '.join('%s: %s' % (c, ', '.join(n)) for c, n in model['conflicts'])) + '.')
    notes.append('Your keymap.conf has %s.' % ('active overrides' if model['overrides'] else
                                              'no active lines, so all shortcuts above are agterm defaults'))
    notes.append('Rebind: edit keymap.conf, then File ▸ Reload Keymap (or: agtermctl keymap reload). '
                 'Check: agtermctl keymap list. %s.' % esc(model['described_for']))
    notes.append('Syntax: map cmd+shift+l toggle_split | global-hotkey ctrl+opt+space | command "Name" ctrl+a>g &lt;shell command&gt;')
    legend = '⌘ Cmd   ⇧ Shift   ⌥ Opt   ⌃ Ctrl' if font_file else 'Cmd, Shift, Opt, Ctrl'

    def page(canvas, doc):
        canvas.setFont('Helvetica-Bold', 20)
        canvas.drawString(margin, height - margin - 12, model['title'])
        canvas.setFont(font, 7.6)
        canvas.setFillColor(colors.HexColor(GREY))
        canvas.drawRightString(width - margin, height - margin - 8, 'current keymap, read from agtermctl keymap list   ' + legend)
        text = canvas.beginText(margin, margin + 40)
        text.setFont(font, 6.8)
        text.setLeading(8.6)
        canvas.setFillColor(colors.HexColor(GREY))
        from reportlab.platypus import Frame as _F
        f = _F(margin, margin - 4, width - 2 * margin, 56, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        f.addFromList([Paragraph(n, note_style) for n in notes], canvas)

    doc = BaseDocTemplate(str(path), pagesize=landscape(A4), title=model['title'])
    doc.addPageTemplates([PageTemplate(id='p', frames=frames, onPage=page)])
    doc.build(story)


def default_output(fmt):
    return state_dir() / 'cheatsheets' / ('agterm-hotkeys.' + fmt)


def command(args):
    fmt = getattr(args, 'format', None) or ('pdf' if have_reportlab() else 'html')
    if fmt == 'pdf' and not have_reportlab():
        raise ValueError('PDF needs reportlab (python3 scripts/install.py --with-pdf --apply). '
                         'Use --format html and print it from a browser, or --format md.')
    output = Path(getattr(args, 'output', None) or default_output(fmt)).expanduser()
    if output.exists() and not args.force:
        raise ValueError('%s exists; pass --force to overwrite or --output elsewhere' % output)
    if args.dry_run:
        print('Would write %s (%s) from agtermctl keymap list' % (output, fmt))
        return 0
    symbols = fmt != 'pdf' or pdf_font() is not None
    model = build(run_list(), symbols=symbols)
    output.parent.mkdir(parents=True, exist_ok=True)
    if fmt == 'pdf':
        render_pdf(model, output)
    else:
        output.write_text(render_md(model) if fmt == 'md' else render_html(model))
    print('Wrote %s' % output)
    print('Personal keymap: do not commit this file.')
    if getattr(args, 'open', False):
        subprocess.run(['open', str(output)])
    return 0
