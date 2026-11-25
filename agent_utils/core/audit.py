"""Audit a project's agent instructions: `agents admin skills audit|extract|prune-rules`.

`audit PROJECT` (the basic check: deterministic, read-only) splits AGENTS.md and every
agent-specific rule file (CLAUDE.md, .claude/rules, AGENTS.override.md, .kiro/steering,
.cursor/rules, Copilot, Gemini and Qwen files) into sections, scores each section as an
always-on rule or as skill-shaped (numbered steps, commands, API calls, a trigger
condition), compares every agent-specific rule file with AGENTS.md line by line (same,
differs, only here), lists the project's skills per agent, and works out which root
skills an agent started in a child repository would miss. AGENTS.md is the source:
agent-specific copies of its text are proposed for removal.

The agent review (optional, `--agent`): one read-only `agents agent ask` run that proposes a skill
name, description and pointer line for each candidate. `--inbox` posts one thread for
the project with one question per candidate and per duplicating rule file.

Nothing in the project changes without a second command: `extract` writes a skill draft
into PROJECT/.agents/skills, links it for Claude and Kiro, and writes a patch for
AGENTS.md (not applied); `prune-rules` removes the lines of agent-specific rule files
that AGENTS.md already has, and deletes a file left empty. Both preview unless --apply.
Reports, patches and backups live in the external state directory.
"""
import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from .paths import state_dir

SOURCE = 'AGENTS.md'
# Agent-specific rule files: (glob relative to the project, agent that loads it).
AGENT_RULES = (('CLAUDE.md', 'claude'), ('.claude/CLAUDE.md', 'claude'), ('CLAUDE.local.md', 'claude'),
               ('.claude/rules/**/*.md', 'claude'), ('AGENTS.override.md', 'codex'),
               ('.kiro/steering/**/*.md', 'kiro-cli'), ('.cursor/rules/**/*.mdc', 'cursor-agent'),
               ('.cursor/rules/**/*.md', 'cursor-agent'), ('.github/copilot-instructions.md', 'copilot'),
               ('.github/instructions/**/*.instructions.md', 'copilot'), ('GEMINI.md', 'agy'), ('QWEN.md', 'qwen'))
# Agents that load AGENTS.md themselves (Claude only through an `@AGENTS.md` import).
READS_SOURCE = ('codex', 'kiro-cli', 'opencode', 'cursor-agent', 'pi', 'kimi', 'copilot', 'vibe')
# Project skill folder per agent and how far up from the start folder it looks:
# 'repo' = start folder and each parent up to the git root, 'cwd' = the start folder only.
# From the Claude Code, Codex and Kiro skills docs (checked 2026-11).
SKILL_DIRS = {'claude': ('.claude/skills', 'repo'), 'codex': ('.agents/skills', 'repo'), 'kiro-cli': ('.kiro/skills', 'cwd')}
SKIP_DIRS = {'node_modules', '.git', 'vendor', 'dist', 'build', '.venv', 'venv', '__pycache__'}
STOP = {'the', 'a', 'an', 'and', 'or', 'of', 'to', 'for', 'in', 'on', 'with', 'how', 'when', 'notes', 'note', 'our'}
TRIGGER = re.compile(r'\b(when (you |we )?(investigat|debug|deploy|releas|migrat|review|test|run|creat|updat|need|work)\w*'
                     r'|before (you |we )?\w+|if you need to|to (investigate|debug|deploy|release|migrate|set up|reproduce))', re.I)
COMMAND = re.compile(r'`[^`\n]*(\s--?\w|\b(git|gh|npm|npx|yarn|pnpm|pip|python3?|make|docker|kubectl|curl|aws|terraform|'
                     r'cargo|go|mvn|gradle|psql|jq|ssh|brew|bash|sh|uv|poetry|bundle|rake)\b)[^`\n]*`')
API = re.compile(r'https?://\S+|\bcurl\b|\b(GET|POST|PUT|PATCH|DELETE) /\S*')
TOOL = re.compile(r'\b(use|run|call) (the )?`?[\w.-]+`? (cli|tool|mcp|script|command|api)\b', re.I)
STEP = re.compile(r'^\s*\d+[.)]\s+\S')
FENCE = re.compile(r'^\s*(```|~~~)')
HEADING = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
MAX_QUESTIONS = 20


# ---------- parsing ----------

def front_matter(text):
    """(meta dict, body, number of front matter lines)."""
    m = re.match(r'---\n(.*?)\n---\n?', text, re.S)
    if not m: return {}, text, 0
    meta = {}
    for line in m.group(1).split('\n'):
        k, sep, v = line.partition(':')
        if sep and k.strip() and not k.startswith(' '): meta[k.strip()] = v.strip().strip('"\'')
    return meta, text[m.end():], m.group(0).count('\n')


def headings(lines):
    """[(index, level, title)] of headings outside fenced code."""
    out, fenced = [], False
    for i, line in enumerate(lines):
        if FENCE.match(line): fenced = not fenced; continue
        m = not fenced and HEADING.match(line)
        if m: out.append((i, len(m.group(1)), m.group(2)))
    return out


def sections(text, offset=0):
    """Sections at the shallowest heading level that occurs twice (or the deepest single one);
    each includes its subsections. Lines are 1-based in the whole file (offset: front matter)."""
    lines = text.split('\n'); hs = headings(lines)
    if not hs: return []
    levels = sorted({lv for _, lv, _ in hs})
    level = next((lv for lv in levels if sum(1 for _, x, _ in hs if x == lv) > 1), levels[-1])
    starts = [(i, t) for i, lv, t in hs if lv == level]
    out = []
    for n, (i, title) in enumerate(starts):
        end = next((j for j, lv, _ in hs if j > i and lv < level), len(lines))
        if n+1 < len(starts): end = min(end, starts[n+1][0])
        body = lines[i+1:end]
        while body and not body[-1].strip(): body.pop()
        out.append({'heading': title, 'level': level, 'start': i+1+offset, 'end': i+1+len(body)+offset,
                    'body': '\n'.join(body)})
    return out


def score(section):
    """(score, signals): high means skill-shaped. Conservative: see is_candidate."""
    body = section['body']; lines = [l for l in body.split('\n') if l.strip()]
    sig = {}
    steps = sum(1 for l in lines if STEP.match(l))
    if steps >= 3: sig['steps'] = steps
    fences = sum(1 for l in lines if FENCE.match(l))//2
    if fences: sig['code blocks'] = fences
    commands = len(COMMAND.findall(body))
    if commands >= 2: sig['commands'] = commands
    if API.search(body): sig['api'] = len(API.findall(body))
    if TOOL.search(body): sig['named tool'] = 1
    head = section['heading']+'\n'+'\n'.join(lines[:3])
    if TRIGGER.search(head) or re.match(r'(how to|investigating|debugging|deploying|releasing|running|setting up)\b',
                                        section['heading'], re.I): sig['trigger'] = 1
    points = (2 if 'steps' in sig else 0)+(2 if fences else 0)+(1 if 'commands' in sig else 0)+(1 if 'api' in sig else 0)
    points += (1 if 'named tool' in sig else 0)+(1 if 'trigger' in sig else 0)+(1 if len(lines) >= 12 else 0)
    bullets = [l for l in lines if re.match(r'^\s*[-*]\s', l)]
    if lines and len(bullets) >= 0.7*len(lines) and not fences and 'commands' not in sig: points -= 1
    return points, sig


def is_candidate(section, threshold=4, min_lines=8):
    nonblank = sum(1 for l in section['body'].split('\n') if l.strip())
    return nonblank >= min_lines and score(section)[0] >= threshold


def slug(text, words=4):
    tokens = [t for t in re.findall(r'[a-z0-9]+', text.lower()) if t not in STOP]
    return '-'.join(tokens[:words]) or 'skill'


def proposal(section, existing=()):
    name = slug(section['heading'])
    text = section['heading']+'\n'+section['body'][:400]; trig = TRIGGER.search(text)
    if trig:
        rest = re.match(r'[^.,:;\n]*', text[trig.end():]).group(0)
        when = (trig.group(0)+rest).strip().lower()[:100]
    else: when = 'when working on '+section['heading'].lower()
    if not when.startswith(('when', 'before', 'if')): when = 'when you need '+when
    out = {'name': name, 'description': '%s. Use %s.' % (section['heading'].rstrip('.'), when),
           'pointer': 'For %s, use the `%s` skill.' % (section['heading'][:1].lower()+section['heading'][1:], name)}
    mine = set(name.split('-'))
    similar = [e for e in existing if mine & (set(e.split('-'))-STOP) and e != name]
    if name in existing: out['exists'] = True
    if similar: out['similar'] = sorted(similar)[:3]
    return out


# ---------- comparison ----------

def norm(line):
    line = re.sub(r'^\s*([-*+>]|\d+[.)])\s+', '', line.strip())
    line = re.sub(r'\s+', ' ', line).strip().lower().rstrip('.:;,')
    return line if len(line) >= 15 and not FENCE.match(line) and not HEADING.match(line) else None


def norm_lines(text):
    return [n for n in (norm(l) for l in text.split('\n')) if n]


def compare(text, source):
    """Line-level comparison of one rule file with the source text."""
    src = norm_lines(source); have = set(src); same, differs, only = [], [], []
    for line in dict.fromkeys(norm_lines(text)):
        if line in have: same.append(line); continue
        close = difflib.get_close_matches(line, src, n=1, cutoff=0.8)
        (differs.append({'here': line, 'source': close[0]}) if close else only.append(line))
    total = len(same)+len(differs)+len(only)
    if total == 0: verdict = 'empty'
    elif len(same) >= 0.9*total and not differs: verdict = 'duplicate'
    elif len(same) >= 3 or len(same) >= 0.3*total: verdict = 'overlaps'
    elif differs: verdict = 'differs'
    else: verdict = 'own'
    return {'verdict': verdict, 'lines': total, 'same': len(same), 'differs': differs, 'only_here': len(only)}


def imports_source(text):
    return bool(re.search(r'^\s*@(\./)?AGENTS\.md\s*$', text, re.M))


# ---------- discovery ----------

def rule_files(project):
    project = Path(project); seen = {}
    for pattern, agent in AGENT_RULES:
        for path in sorted(project.glob(pattern)):
            if path.is_file() and path not in seen: seen[path] = agent
    return seen


def git_root(path):
    try: done = subprocess.run(['git', '-C', str(path), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError): return None
    return Path(done.stdout.strip()).resolve() if done.returncode == 0 and done.stdout.strip() else None


def child_repos(root, depth=2):
    found = []
    def walk(d, left):
        try: entries = sorted(e for e in d.iterdir() if e.is_dir() and not e.is_symlink())
        except OSError: return
        for e in entries:
            if e.name in SKIP_DIRS or e.name.startswith('.'): continue
            if (e/'.git').exists(): found.append(e)
            elif left > 1: walk(e, left-1)
    walk(Path(root), depth)
    return found


def listed(directory):
    d = Path(directory)
    if not d.is_dir(): return []
    return sorted(p.name for p in d.iterdir() if not p.name.startswith('.') and (p.is_symlink() or (p/'SKILL.md').is_file()))


def searched(start, mode, top=None):
    """Folders an agent started in START scans for project skills."""
    start = Path(start).resolve()
    if mode == 'cwd': return [start]
    top = top if top is not None else git_root(start)
    if not top or (top != start and top not in start.parents): return [start]
    out, d = [], start
    while True:
        out.append(d)
        if d == top: return out
        d = d.parent


def visibility(project, roots=git_root):
    """Per agent, the skills seen at the root, and per child repository the root skills it misses."""
    root = Path(project).resolve(); report = {'root': {}, 'children': []}
    for agent, (rel, _) in SKILL_DIRS.items(): report['root'][agent] = listed(root/rel)
    for child in child_repos(root):
        top = roots(child); row = {'path': str(child.relative_to(root)), 'missing': {}, 'own': {}}
        for agent, (rel, mode) in SKILL_DIRS.items():
            dirs = searched(child, mode, top)
            row['own'][agent] = listed(child/rel)
            if root not in dirs and report['root'][agent]: row['missing'][agent] = report['root'][agent]
        report['children'].append(row)
    return report


def existing_skills(project, home=None):
    home = Path(home or Path.home()); names = set()
    for rel in ('.agents/skills', '.claude/skills', '.kiro/skills'):
        names |= set(listed(Path(project)/rel)) | set(listed(home/rel))
    return sorted(names)


# ---------- audit ----------

def audit(project, home=None, roots=git_root):
    from . import skills as sk
    project = Path(project).expanduser().resolve()
    if not project.is_dir(): raise ValueError('Not a directory: %s' % project)
    src_path = project/SOURCE; source = src_path.read_text(errors='replace') if src_path.is_file() else ''
    existing = existing_skills(project, home)
    files = [(src_path, 'shared')] if source else []
    files += list(rule_files(project).items())
    report = {'project': str(project), 'source': SOURCE if source else None, 'files': [], 'candidates': [], 'rules': [],
              'pairs': [], 'no_source': [], 'skills': [], 'visibility': visibility(project, roots)}
    texts = {}
    for path, agent in files:
        raw = path.read_text(errors='replace'); meta, body, off = front_matter(raw)
        rel = str(path.relative_to(project)); texts[rel] = body
        entry = {'file': rel, 'agent': agent, 'lines': raw.count('\n')+1}
        if meta.get('inclusion'): entry['inclusion'] = meta['inclusion']
        if 'alwaysApply' in meta: entry['alwaysApply'] = meta['alwaysApply']
        report['files'].append(entry)
        for sec in sections(body, off):
            if is_candidate(sec):
                pts, sig = score(sec)
                report['candidates'].append(dict({'file': rel, 'agent': agent, 'heading': sec['heading'], 'start': sec['start'],
                                                  'end': sec['end'], 'score': pts, 'signals': sig}, **proposal(sec, existing)))
        if agent == 'shared': continue
        if agent == 'claude' and imports_source(raw): entry['imports'] = SOURCE
        if source:
            cmp = compare(body, source); cmp.update(file=rel, agent=agent)
            if entry.get('imports') and not cmp['only_here'] and not cmp['differs']: cmp['verdict'] = 'imports'
            cmp['action'] = {'duplicate': 'remove the file (AGENTS.md is the source)',
                             'overlaps': 'prune the lines AGENTS.md already has; move generic leftovers into AGENTS.md',
                             'differs': 'reconcile the lines that differ from AGENTS.md',
                             'own': 'keep (agent-specific)', 'empty': 'keep (nothing to compare)',
                             'imports': 'keep (loads AGENTS.md through @AGENTS.md)'}[cmp['verdict']]
            report['rules'].append(cmp)
    agent_files = [f for f in report['files'] if f['agent'] != 'shared']
    have = set(norm_lines(source))
    for i, a in enumerate(agent_files):
        for b in agent_files[i+1:]:
            if a['agent'] == b['agent']: continue
            shared = (set(norm_lines(texts[a['file']])) & set(norm_lines(texts[b['file']]))) - have
            if len(shared) >= 3: report['pairs'].append({'files': [a['file'], b['file']], 'lines': len(shared)})
    if source:
        for agent in ('claude', 'agy', 'qwen'):
            mine = [f for f in report['files'] if f['agent'] == agent]
            if agent == 'claude' and mine and not any(f.get('imports') for f in mine): report['no_source'].append(agent)
            elif agent != 'claude' and mine: report['no_source'].append(agent)
    rows = sk.status(sk.Env(home=home, project=project))
    for row in rows:
        loose = {a: s for a, s in row['agents'].items() if s in ('same', 'differs', 'only-here', 'missing', 'broken')}
        if loose or row['store'] != 'yes': report['skills'].append({'name': row['name'], 'store': row['store'], 'agents': row['agents']})
    return report


def stage_b_prompt(report):
    lines = ['Read-only review of a project\'s agent instructions. For each candidate section below, read the lines '
             'and decide: extract it as a project skill (in .agents/skills), keep it in the always-on instructions, '
             'or merge it into an existing skill. Existing skills: %s.' % (', '.join(existing_skills(report['project'])) or 'none'),
             'Answer with one JSON object per candidate, one per line: {"id": N, "verdict": "extract|keep|merge", '
             '"name": "kebab-name", "description": "what it does. Use when ... (trigger words first)", '
             '"merge_into": null, "pointer": "the one line that stays in AGENTS.md", "reason": "one sentence"}.', '']
    for n, c in enumerate(report['candidates'], 1):
        lines.append('%d. %s lines %d-%d, heading "%s" (signals: %s; draft name %s)' % (
            n, c['file'], c['start'], c['end'], c['heading'], ', '.join(c['signals']), c['name']))
    return '\n'.join(lines)


def stage_b(report, spec, timeout):
    from . import ask
    if not report['candidates']: return None
    return ask.ask(spec, stage_b_prompt(report), cwd=report['project'], timeout=timeout)


def render(report):
    out = ['# Instructions audit: %s' % Path(report['project']).name, '',
           'Project: `%s`. Source of shared rules: %s.' % (report['project'], report['source'] or 'none (no AGENTS.md)'), '']
    out += ['## Instruction files', '']
    for f in report['files']:
        extra = ', '.join('%s %s' % (k, f[k]) for k in ('inclusion', 'alwaysApply', 'imports') if k in f)
        out.append('- `%s` (%s, %d lines%s)' % (f['file'], f['agent'], f['lines'], ', '+extra if extra else ''))
    if report['no_source']:
        out.append('- Has its own rule file but does not load AGENTS.md: %s (for Claude add `@AGENTS.md` to CLAUDE.md)' % ', '.join(report['no_source']))
    out += ['', '## Skill-shaped sections', '']
    if not report['candidates']: out.append('None found (threshold is conservative).')
    for n, c in enumerate(report['candidates'], 1):
        out.append('%d. `%s` lines %d-%d, "%s": score %d (%s)' % (n, c['file'], c['start'], c['end'], c['heading'], c['score'],
                                                               ', '.join('%s %s' % kv for kv in c['signals'].items())))
        out.append('   - Draft: `%s`: %s' % (c['name'], c['description']))
        out.append('   - Pointer left in place: %s' % c['pointer'])
        if c.get('exists'): out.append('   - A skill named `%s` already exists; consider merging.' % c['name'])
        if c.get('similar'): out.append('   - Similar existing skills: %s' % ', '.join('`%s`' % s for s in c['similar']))
        out.append('   - Extract (preview): `agents admin skills extract %s --file %s --section L%d --name %s`' % (
            _q(report['project']), _q(c['file']), c['start'], c['name']))
    out += ['', '## Agent-specific rules compared with AGENTS.md', '']
    if not report['rules']: out.append('None.' if report['source'] else 'No AGENTS.md to compare with.')
    for r in report['rules']:
        out.append('- `%s` (%s): **%s**, %d of %d lines also in AGENTS.md, %d differ, %d only here. Proposed: %s.' % (
            r['file'], r['agent'], r['verdict'], r['same'], r['lines'], len(r['differs']), r['only_here'], r['action']))
        for d in r['differs'][:5]: out += ['  - here: %s' % d['here'], '    AGENTS.md: %s' % d['source']]
    for p in report['pairs']:
        out.append('- `%s` and `%s` share %d lines AGENTS.md lacks: move them into AGENTS.md.' % (p['files'][0], p['files'][1], p['lines']))
    if any(r['verdict'] in ('duplicate', 'overlaps') for r in report['rules']):
        out.append('\nPrune (preview): `agents admin skills prune-rules %s`' % _q(report['project']))
    out += ['', '## Project skills', '']
    if not report['skills']: out.append('All project skills are in `.agents/skills` and linked for Claude and Kiro (or none exist).')
    for s in report['skills']:
        out.append('- `%s`: store %s; %s' % (s['name'], s['store'], ', '.join('%s %s' % (a, st or '-') for a, st in s['agents'].items())))
    if report['skills']: out.append('\nMove copies into `.agents/skills` and link: `agents admin skills --project %s sync` (preview).' % _q(report['project']))
    vis = report['visibility']
    out += ['', '## Visibility from child repositories', '',
            'From the Claude Code, Codex and Kiro docs: Claude and Codex look for project skills from the '
            'start folder up to its git root, Kiro only in the start folder. A child with its own repository does not see the root\'s skills.', '']
    out.append('- Root: ' + '; '.join('%s %s' % (a, ', '.join(v) or 'none') for a, v in vis['root'].items()))
    if not vis['children']: out.append('- No child repositories.')
    for c in vis['children']:
        miss = '; '.join('%s misses %d (%s)' % (a, len(v), ', '.join(v[:5])) for a, v in c['missing'].items())
        out.append('- `%s`: %s' % (c['path'], miss or 'sees the root skills'))
    if any(c['missing'] for c in vis['children']):
        out.append('\nFix: make those skills user-level (`agents admin skills add DIR --apply`) or link them into each child.')
    if report.get('stage_b'):
        out += ['', '## Agent answers', '']
        for r in report['stage_b']['results']: out.append('- %s: %s, `%s`' % (r['target'], r['status'], r.get('answer', '')))
    return '\n'.join(out)+'\n'


def _q(text):
    import shlex
    return shlex.quote(str(text))


def report_dir(project, base=None):
    from datetime import datetime
    project = Path(project).resolve()
    tag = '%s-%s' % (re.sub(r'[^\w.-]', '_', project.name), hashlib.sha256(str(project).encode()).hexdigest()[:8])
    d = Path(base or state_dir()/'audits')/tag/datetime.now().strftime('%Y%m%d-%H%M%S')
    d.mkdir(parents=True, exist_ok=True)
    return d


def inbox_body(report):
    lines = ['Audit of `%s` (AGENTS.md is the source). Full report attached; nothing is changed until you answer.' % report['project'], '']
    asked = 0
    for c in report['candidates']:
        if asked >= MAX_QUESTIONS: break
        asked += 1
        merge = ''.join('- [ ] Merge into `%s`\n' % s for s in (c.get('similar') or [])[:2])
        lines.append(':::question\nExtract "%s" (`%s` lines %d-%d) as a skill?\n\n- [ ] Extract as `%s`\n- [ ] Keep in %s\n%s\n'
                     'Recommended: Extract as `%s`\n:::\n' % (c['heading'], c['file'], c['start'], c['end'], c['name'],
                                                            c['file'], merge, c['name']))
    for r in report['rules']:
        if asked >= MAX_QUESTIONS or r['verdict'] not in ('duplicate', 'overlaps', 'differs'): continue
        asked += 1
        fix = 'Remove the file' if r['verdict'] == 'duplicate' else 'Remove the lines AGENTS.md has' if r['verdict'] == 'overlaps' else 'Make it match AGENTS.md'
        lines.append(':::question\n`%s` (%s) %s AGENTS.md: %d of %d lines the same, %d differ. What should happen?\n\n'
                     '- [ ] %s\n- [ ] Keep as is\n\nRecommended: %s\n:::\n' % (
                         r['file'], r['agent'], 'duplicates' if r['verdict'] == 'duplicate' else 'overlaps with' if r['verdict'] == 'overlaps' else 'differs from',
                         r['same'], r['lines'], len(r['differs']), fix, fix))
    left = len(report['candidates'])+sum(1 for r in report['rules'] if r['verdict'] in ('duplicate', 'overlaps', 'differs'))-asked
    if left > 0: lines.append('%d more findings are in the report only.' % left)
    if not asked: lines.append('No skill-shaped sections or duplicated rules found.')
    return '\n'.join(lines)


def send_inbox(report, attachments, runner=subprocess.run):
    body = inbox_body(report)
    payload = {'subject': 'Instructions audit: %s' % Path(report['project']).name, 'thread': 'Instructions audit: %s' % Path(report['project']).name,
               'body': body, 'attachments': [str(a) for a in attachments],
               'idempotency_key': 'agents-audit-'+hashlib.sha256((report['project']+body).encode()).hexdigest()[:20]}
    env = dict(os.environ, PLANNOTATOR_CWD=report['project'])
    done = runner(['plannotator', 'inbox', 'call', 'send_message', '-'], input=json.dumps(payload), capture_output=True,
                  text=True, cwd=report['project'], env=env, timeout=60)
    if done.returncode: raise ValueError('Inbox send failed: '+(done.stderr or '').strip()[:300])
    try: return json.loads(done.stdout)
    except ValueError: return {'raw': done.stdout.strip()}


# ---------- extract ----------

def find_section(text, section):
    meta, body, off = front_matter(text); lines = text.split('\n')
    hs = headings(lines)
    want = section.strip()
    hit = None
    if re.fullmatch(r'L?\d+', want):
        n = int(want.lstrip('L'))-1; hit = next(((i, lv, t) for i, lv, t in hs if i == n), None)
    else:
        found = [h for h in hs if h[2].strip().lower() == want.lower()]
        if len(found) > 1: raise ValueError('Heading %r occurs %d times; give its line number (L%d)' % (want, len(found), found[0][0]+1))
        hit = found[0] if found else None
    if not hit: raise ValueError('No heading %r' % section)
    i, level, title = hit
    end = next((j for j, lv, _ in hs if j > i and lv <= level), len(lines))
    while end > i+1 and not lines[end-1].strip(): end -= 1
    return i, end, level, title


def extract(project, file, section, name, description=None, dry=True, home=None, base=None):
    from . import skills as sk
    project = Path(project).expanduser().resolve(); path = project/file
    if not path.is_file(): raise ValueError('No file %s' % path)
    sk._check_name(name)
    text = path.read_text(); lines = text.split('\n')
    i, end, level, title = find_section(text, section)
    body = lines[i+1:end]
    shifted = []
    fenced = False
    for line in body:
        if FENCE.match(line): fenced = not fenced
        m = not fenced and HEADING.match(line)
        shifted.append('#'*max(1, len(m.group(1))-level+1)+' '+m.group(2) if m else line)
    sec = {'heading': title, 'body': '\n'.join(body)}
    prop = proposal(sec)
    desc = (description or prop['description']).replace('\n', ' ').strip()
    skill = '---\nname: %s\ndescription: %s\n---\n\n# %s\n\n%s\n' % (name, json.dumps(desc) if ':' in desc else desc, title, '\n'.join(shifted).strip('\n'))
    pointer = 'For %s, use the `%s` skill.' % (title[:1].lower()+title[1:], name)
    new = lines[:i+1]+['', pointer]+([''] if end < len(lines) and lines[end].strip() else [])+lines[end:]
    patch = ''.join(difflib.unified_diff(text.splitlines(True), '\n'.join(new).splitlines(True), 'a/'+file, 'b/'+file))
    env = sk.Env(home=home, project=project)
    dest = env.store/name
    if dest.exists() or dest.is_symlink(): raise ValueError('%s already exists; merge by hand or pick another --name' % dest)
    actions = [{'action': 'write', 'skill': name, 'path': str(dest/'SKILL.md')}]
    if dry:
        actions += [{'action': 'link', 'skill': name, 'agent': a, 'path': str(d/name)} for a, d in env.dirs.items()]
        return actions, skill, patch, None
    write_text(dest/'SKILL.md', skill)
    actions += sk.link(env, dry=False, names=[name])
    patch_path = report_dir(project, base)/(name+'.patch'); patch_path.write_text(patch)
    return actions, skill, patch, patch_path


def write_text(path, text):
    """Write TEXT to PATH through a temporary file in the same folder."""
    import tempfile
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.agent-utils-')
    try:
        with os.fdopen(fd, 'w') as stream: stream.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


# ---------- prune ----------

def pruned(raw, source):
    """RAW without the lines SOURCE already has, keeping front matter, fences and non-empty sections."""
    have = set(norm_lines(source)); meta, body, off = front_matter(raw)
    head = raw[:len(raw)-len(body)]; lines = body.split('\n'); keep = []; i = 0
    while i < len(lines):
        line = lines[i]
        if FENCE.match(line):
            j = next((k for k in range(i+1, len(lines)) if FENCE.match(lines[k])), len(lines)-1)
            block = lines[i:j+1]; inner = [norm(l) for l in block[1:-1]]
            if not (inner and all(n is None or n in have for n in inner) and any(inner)): keep += block
            i = j+1; continue
        n = norm(line)
        if not (n and n in have): keep.append(line)
        i += 1
    # drop headings whose section is now empty
    out = []
    for k, line in enumerate(keep):
        m = HEADING.match(line)
        if m:
            nxt = next((l for l in keep[k+1:] if l.strip()), None)
            nm = nxt and HEADING.match(nxt)
            if nxt is None or (nm and len(nm.group(1)) <= len(m.group(1))): continue
        out.append(line)
    text = re.sub(r'\n{3,}', '\n\n', '\n'.join(out)).strip('\n')
    content = any(l.strip() and not HEADING.match(l) for l in out)
    return (head+text+'\n') if content else None


def prune(project, files=None, dry=True, base=None):
    from datetime import datetime
    project = Path(project).expanduser().resolve(); src = project/SOURCE
    if not src.is_file(): raise ValueError('No %s in %s; nothing is the source' % (SOURCE, project))
    source = src.read_text(errors='replace'); found = rule_files(project)
    chosen = [project/f for f in files] if files else list(found)
    backups = Path(base or state_dir()/'backups')/datetime.now().strftime('%Y%m%dT%H%M%S')/'rules'
    actions = []; diffs = []
    for path in chosen:
        rel = str(path.relative_to(project))
        if path not in found: raise ValueError('%s is not an agent-specific rule file' % rel)
        raw = path.read_text(errors='replace')
        if found[path] == 'claude' and imports_source(raw) and not files: continue
        new = pruned(raw, source)
        if new == raw: continue
        diffs.append(''.join(difflib.unified_diff(raw.splitlines(True), (new or '').splitlines(True), 'a/'+rel, 'b/'+rel)))
        actions.append({'action': 'delete' if new is None else 'prune', 'path': rel})
        if not dry:
            target = backups/rel; target.parent.mkdir(parents=True, exist_ok=True); target.write_text(raw)
            if new is None: path.unlink()
            else: write_text(path, new)
            actions[-1]['backup'] = str(target)
    return actions, ''.join(diffs)


# ---------- command ----------

def command(argv):
    p = argparse.ArgumentParser(prog='agents admin skills', description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='operation', required=True)
    a = sub.add_parser('audit', help='report skill-shaped sections, duplicated rules and skill visibility (read-only)')
    a.add_argument('project'); a.add_argument('--json', action='store_true')
    a.add_argument('--agent', help='agents to ask for proposals (as `agents agent ask`: claude, codex, kiro, a,b or all)')
    a.add_argument('--prompt', action='store_true', help='print the agent prompt instead of asking an agent')
    a.add_argument('--timeout', type=float, default=900)
    a.add_argument('--inbox', action='store_true', help='post one Inbox thread with a question per finding')
    e = sub.add_parser('extract', help='write a skill draft from a section, link it, write an AGENTS.md patch')
    e.add_argument('project'); e.add_argument('--file', default=SOURCE); e.add_argument('--section', required=True, help='heading text or L<line>')
    e.add_argument('--name', required=True); e.add_argument('--description'); e.add_argument('--apply', action='store_true')
    r = sub.add_parser('prune-rules', help='remove lines of agent-specific rule files that AGENTS.md already has')
    r.add_argument('project'); r.add_argument('files', nargs='*', help='only these (project-relative); default: all, except CLAUDE.md files importing AGENTS.md')
    r.add_argument('--apply', action='store_true')
    args = p.parse_args(argv)
    if args.operation == 'audit':
        report = audit(args.project)
        if args.prompt: print(stage_b_prompt(report)); return 0
        if args.agent: report['stage_b'] = stage_b(report, args.agent, args.timeout)
        out = report_dir(report['project']); text = render(report)
        (out/'report.md').write_text(text); (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        attachments = [out/'report.md']+[Path(r['answer']) for r in (report.get('stage_b') or {}).get('results', [])
                                           if r.get('status') == 'ok' and Path(r.get('answer', '')).is_file()]
        print(json.dumps(report, indent=2) if args.json else text, end='' if not args.json else '\n')
        print('Report: %s' % (out/'report.md'))
        if args.inbox:
            sent = send_inbox(report, attachments)
            print('Inbox: %s' % (sent.get('url') or sent.get('thread_id') or sent))
        return 0
    if args.operation == 'extract':
        actions, skill, patch, patch_path = extract(args.project, args.file, args.section, args.name, args.description, not args.apply)
        for x in actions: print('  %-6s %-30s %-10s %s' % (x['action'], x.get('skill', ''), x.get('agent', ''), x.get('path', '')))
        if not args.apply:
            print('\n--- SKILL.md ---\n'+skill+'\n--- patch for %s (written on --apply, not applied) ---\n%s' % (args.file, patch))
            print('Preview only; add --apply to write the skill and the patch.')
        else:
            print('Patch for %s: %s\nReview, then: git -C %s apply %s' % (args.file, patch_path, _q(Path(args.project).resolve()), _q(patch_path)))
        return 0
    actions, diff = prune(args.project, args.files or None, not args.apply)
    if diff: print(diff)
    for x in actions: print('  %-6s %s%s' % (x['action'], x['path'], ' (backup %s)' % x['backup'] if x.get('backup') else ''))
    if not actions: print('Nothing to prune.')
    elif not args.apply: print('Preview only; add --apply to change the files (backups go to the state folder).')
    return 0
