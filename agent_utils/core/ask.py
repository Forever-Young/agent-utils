"""Ask other agents for a read-only second opinion: `agents agent ask`.

`agents agent ask TARGET[,TARGET...]|all "PROMPT"` runs each target agent once,
non-interactively, in its read-only mode, in parallel. Each answer goes to
<state>/ask/<run>/<target>.md; stdout gets one JSON line describing the run.

Every adapter below names the flags it relies on. Before a run, the target's own
help output is checked for those flags; a target whose CLI lacks one is reported
`unsupported` and not started. `agents agent ask check` shows that check without asking.

Guards: embedded files and diffs refuse secret-looking paths and private key
content; embedded context and each answer are capped in size; each target has a
timeout (its whole process group is killed); AGENT_UTILS_ASK_DEPTH stops an asked
agent from asking again. Children get no agterm or agent session variables, so
their hooks cannot rename or mark the caller's agterm row.

Only review (read-only) mode exists. A write-capable task mode is not implemented.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import tempfile
import time

from .agents import AGENT_NAMES, AGENTS, agent_executable
from .paths import state_dir

DEPTH_VAR = 'AGENT_UTILS_ASK_DEPTH'
MAX_DEPTH_VAR = 'AGENT_UTILS_ASK_MAX_DEPTH'
ALIASES = {'kiro': 'kiro-cli', 'cursor': 'cursor-agent', 'antigravity': 'agy', 'gemini': 'agy'}
CONTEXT_CAP = 200_000      # bytes of embedded files and diff
ANSWER_CAP = 1_000_000     # bytes kept per answer
DEFAULT_TIMEOUT = 900
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07')

# agent: (help argv after the executable, flags that must appear in it, argv builder).
# The builder gets (prompt, cwd, answer path) and returns argv after the executable.
# `answer` set means the CLI writes the final message to that file itself.
ADAPTERS = {
    'claude': (['--help'], ['--print', '--tools', '--permission-mode', '--permission-prompts', '--strict-mcp-config',
                            '--no-session-persistence', '--output-format'],
               lambda p, cwd, out: ['--print', '--output-format', 'text', '--tools', 'Read,Grep,Glob',
                                    '--permission-mode', 'default', '--permission-prompts', 'none',
                                    '--strict-mcp-config', '--no-session-persistence', '--', p]),
    'codex': (['exec', '--help'], ['--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral', '--color', '--cd',
                                   '--output-last-message'],
              lambda p, cwd, out: ['exec', '--sandbox', 'read-only', '--skip-git-repo-check', '--ephemeral',
                                   '--color', 'never', '--cd', cwd, '--output-last-message', str(out), '--', p]),
    'kiro-cli': (['chat', '--help'], ['--no-interactive', '--trust-tools', '--trust-workspace'],
                 lambda p, cwd, out: ['chat', '--no-interactive', '--trust-tools=fs_read,grep,glob,code',
                                      '--trust-workspace', '--', p]),
    'opencode': (['run', '--help'], ['--agent', '--archived'],
                 lambda p, cwd, out: ['run', '--agent', 'plan', '--archived', p]),
    'cursor-agent': (['--help'], ['--print', '--mode', 'ask', '--output-format', '--workspace'],
                     lambda p, cwd, out: ['--print', '--mode', 'ask', '--output-format', 'text',
                                          '--workspace', cwd, p]),
    'pi': (['--help'], ['--print', '--tools', '--no-session', '--no-mcp'],
           lambda p, cwd, out: ['--print', '--no-session', '--no-mcp', '--tools', 'read,grep,find,ls', '--', p]),
    'agy': (['--help'], ['--print', '--mode', 'plan', '--sandbox'],
            lambda p, cwd, out: ['--mode', 'plan', '--sandbox', '--print', p]),
    'kimi': (['--help'], ['--plan', '--prompt'],
             lambda p, cwd, out: ['--plan', '--prompt', p]),
    'qwen': (['--help'], ['--print', '--permission-mode', '--disallowed-tools', '--no-session-persistence'],
             lambda p, cwd, out: ['--print', '--permission-mode', 'default', '--no-session-persistence',
                                  '--disallowed-tools=edit', '--disallowed-tools=write_file',
                                  '--disallowed-tools=run_shell_command', '--', p]),
    'copilot': (['--help'], ['--prompt', '--silent', '--no-ask-user', '--deny-tool'],
                lambda p, cwd, out: ['--silent', '--no-ask-user', '--deny-tool=write', '--prompt', p]),
    'vibe': (['--help'], ['--prompt', '--enabled-tools', '--trust', '--workdir', '--output'],
             lambda p, cwd, out: ['--workdir', cwd, '--trust', '--output', 'text', '--enabled-tools', 'read_file',
                                  '--enabled-tools', 'grep', '--prompt', p]),
}
assert set(ADAPTERS) == set(AGENTS)
WRITES_ANSWER = {'codex'}  # writes its last message to the answer file; stdout is an event log

# Instruction files an agent loads by itself (project-relative, or ~/ for global).
# Conservative: a file missing here is only listed in the preamble, read twice at worst.
NATIVE = {
    'claude': ('CLAUDE.md', 'CLAUDE.local.md', '.claude/CLAUDE.md', '.claude/rules', '~/.claude/CLAUDE.md', '~/.claude/rules'),
    'codex': ('AGENTS.md', '~/.codex/AGENTS.md'),
    'kiro-cli': ('AGENTS.md', '.kiro/steering', '~/.kiro/steering'),
    'opencode': ('AGENTS.md', '~/.config/opencode/AGENTS.md'),
    'cursor-agent': ('AGENTS.md', '.cursor/rules'),
    'pi': ('AGENTS.md', 'CLAUDE.md', '~/.pi/agent/AGENTS.md'),
    'agy': ('GEMINI.md', '~/.gemini/GEMINI.md'),
    'kimi': ('AGENTS.md',),
    'qwen': ('QWEN.md', '~/.qwen/QWEN.md'),
    'copilot': ('AGENTS.md', '.github/copilot-instructions.md'),
    'vibe': ('AGENTS.md',),
}
PROJECT_FILES = ('AGENTS.md', 'CLAUDE.md', 'CLAUDE.local.md', '.claude/CLAUDE.md', '.claude/rules', '.kiro/steering',
                 'GEMINI.md', 'QWEN.md', '.cursor/rules', '.github/copilot-instructions.md')
SECRET_NAME = re.compile(r'(^|/)(\.env(\..*)?|\.envrc|\.netrc|\.npmrc|\.pypirc|id_(rsa|dsa|ecdsa|ed25519)[^/]*|'
                         r'[^/]*\.(pem|key|p8|p12|pfx|keystore|jks)|credentials[^/]*|secrets?\.[^/]*|auth\.json)$', re.I)
SECRET_TEXT = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')


def resolve(name):
    name = ALIASES.get(name.strip().lower(), name.strip().lower())
    if name not in AGENTS:raise ValueError('Unknown agent %r; choose from %s' % (name, ', '.join(AGENTS)))
    return name


def caller(env=None):
    from ..agterm.names import detect
    return detect(env)[0]


def targets(spec, env=None, which=agent_executable):
    """Explicit names keep their order; `all` means installed agents except the caller."""
    if spec.strip().lower() == 'all':
        me = caller(env)
        return [a for a in AGENTS if a != me and which(a)]
    return list(dict.fromkeys(resolve(x) for x in spec.split(',') if x.strip()))


def check(agent, executable, timeout=20):
    """Missing adapter flags in the installed CLI's own help ([] when all are there)."""
    help_argv, flags, _ = ADAPTERS[agent]
    try:
        done = subprocess.run([executable]+help_argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout, env=child_env(os.environ))
    except (OSError, subprocess.SubprocessError) as exc:
        return ['(help failed: %s)' % exc]
    text = done.stdout+done.stderr
    return [f for f in flags if not re.search(r'(?<![\w-])'+re.escape(f)+r'(?![\w-])', text)]


def child_env(env, depth=None):
    """The caller's environment without agterm and agent session identity."""
    from ..agterm.names import ENV, MARKERS
    drop = {var for _, var in ENV+MARKERS}|{'CLAUDE_ENV_FILE', 'QWEN_ENV_FILE'}
    out = {k: v for k, v in env.items() if k not in drop and not k.startswith('AGTERM_')}
    if depth is not None:out[DEPTH_VAR] = str(depth)
    return out


def depth_guard(env):
    try:depth, limit = int(env.get(DEPTH_VAR) or 0), int(env.get(MAX_DEPTH_VAR) or 1)
    except ValueError:raise ValueError(DEPTH_VAR+' or '+MAX_DEPTH_VAR+' is not a number')
    if depth >= limit:
        raise ValueError('Refusing a nested ask: this agent was itself asked (%s=%d). Answer from your own analysis.' % (DEPTH_VAR, depth))
    return depth


def _git_root(cwd):
    try:
        done = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):return None
    return Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None


def instruction_files(agent, cwd, me=None, home=None):
    """Existing instruction files `agent` would not load itself: the project's (cwd and its
    git root), and the caller's global ones. Absolute paths, so no shell expansion is needed."""
    home = Path(home or Path.home()); cwd = Path(cwd).resolve()
    native = NATIVE[agent]
    dirs = list(dict.fromkeys([cwd]+[d for d in [_git_root(cwd)] if d]))
    found = [d/rel for d in dirs for rel in PROJECT_FILES if rel not in native and (d/rel).exists()]
    if me and me != agent:
        found += [home/g[2:] for g in NATIVE[me] if g.startswith('~/') and g not in native and (home/g[2:]).exists()]
    return [str(p) for p in dict.fromkeys(found)]


def secret_reason(name, text=''):
    if SECRET_NAME.search(str(name).replace(os.sep, '/')):return 'secret-looking file name'
    if SECRET_TEXT.search(text):return 'contains a private key'
    return None


def context(cwd, files=(), diff=None):
    """Embedded file and diff text, refusing secrets, capped at CONTEXT_CAP bytes."""
    parts = []
    for name in files:
        path = (Path(cwd)/name).expanduser()
        if secret_reason(path):raise ValueError('Refusing to embed %s: %s' % (name, secret_reason(path)))
        text = path.read_text(errors='replace')
        if secret_reason('', text):raise ValueError('Refusing to embed %s: %s' % (name, secret_reason('', text)))
        parts.append('## File: %s\n\n```\n%s\n```' % (name, text))
    if diff:
        if diff.startswith('-'):raise ValueError('Invalid --diff value')
        base = ['git', '-C', str(cwd), 'diff']+({'unstaged': [], 'staged': ['--cached']}.get(diff, [diff]))
        names = subprocess.run(base+['--name-only'], capture_output=True, text=True, timeout=30)
        if names.returncode:raise ValueError('git diff failed: '+names.stderr.strip()[:200])
        bad = [n for n in names.stdout.split('\n') if n and secret_reason(n)]
        if bad:raise ValueError('Refusing to embed a diff touching secret-looking files: '+', '.join(bad))
        text = subprocess.run(base, capture_output=True, text=True, timeout=60).stdout
        if secret_reason('', text):raise ValueError('Refusing to embed the diff: it contains a private key')
        parts.append('## Diff (%s)\n\n```diff\n%s\n```' % (diff, text or '(empty)'))
    body = '\n\n'.join(parts)
    if len(body.encode()) > CONTEXT_CAP:
        raise ValueError('Embedded context is %d bytes, over the %d byte limit; pass paths in the prompt instead'
                         % (len(body.encode()), CONTEXT_CAP))
    return body


def preamble(agent, cwd, me=None, home=None):
    asker = AGENT_NAMES.get(me, 'another coding agent') if me else 'another coding agent'
    lines = ['You are consulted by %s for a second opinion. Work read-only: do not edit files, commit, '
             'or run commands that change state. Answer the request below; the asking agent decides and '
             'implements. Do not ask other agents.' % asker]
    files = instruction_files(agent, cwd, me, home)
    if files:lines.append('First read these instruction files, which you do not load yourself: '+', '.join(files))
    return '\n\n'.join(lines)


def build(agent, executable, prompt, cwd, out, extra=()):
    """Adapter argv with `extra` inserted before the prompt (and its `--`, `--prompt` or `--print`)."""
    argv = ADAPTERS[agent][2](prompt, str(cwd), out)
    cut = len(argv)-(2 if len(argv) > 1 and argv[-2] in ('--', '--prompt', '--print') else 1)
    return [executable]+argv[:cut]+list(extra)+argv[cut:]


def _kill(proc):
    try:os.killpg(proc.pid, signal.SIGTERM)
    except OSError:pass
    try:proc.wait(5)
    except subprocess.TimeoutExpired:
        try:os.killpg(proc.pid, signal.SIGKILL)
        except OSError:pass
        proc.wait()


def run_one(agent, executable, prompt, cwd, run_dir, env, timeout, extra=(), cap=ANSWER_CAP):
    answer, log, raw = run_dir/(agent+'.md'), run_dir/(agent+'.log'), run_dir/(agent+'.out')
    result = {'target': agent, 'answer': str(answer)}
    missing = check(agent, executable)
    if missing:
        result.update(status='unsupported', missing_flags=missing)
        return result
    writes_file = agent in WRITES_ANSWER
    argv = build(agent, executable, prompt, cwd, answer, extra)
    started = time.monotonic(); status = 'ok'
    with open(raw, 'wb') as out, open(log, 'wb') as err:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                env=env, start_new_session=True)
        while proc.poll() is None:
            if time.monotonic()-started > timeout:status = 'timeout';_kill(proc);break
            if raw.stat().st_size > cap*2:status = 'too-large';_kill(proc);break
            time.sleep(0.2)
    result.update(exit_code=proc.returncode, seconds=round(time.monotonic()-started, 1))
    text = answer.read_text(errors='replace') if writes_file and answer.exists() else raw.read_text(errors='replace')
    text = ANSI.sub('', text).strip()
    data = text.encode()
    result['truncated'] = len(data) > cap
    if result['truncated']:text = data[:cap].decode(errors='ignore')+'\n\n[answer truncated at %d bytes]' % cap
    answer.write_text(text+'\n')
    if not writes_file:raw.unlink()
    result['bytes'] = len(text.encode())
    if status == 'ok' and proc.returncode != 0:status = 'failed'
    if status == 'ok' and not text:status = 'empty'
    result['status'] = status
    if status != 'ok':
        tail = ANSI.sub('', log.read_text(errors='replace')).strip()[-600:]
        if tail:result['stderr_tail'] = tail
    return result


def ask(spec, prompt, cwd='.', files=(), diff=None, timeout=DEFAULT_TIMEOUT, extra=None, env=None,
        which=agent_executable, base=None):
    env = dict(os.environ if env is None else env)
    depth = depth_guard(env)
    if not prompt.strip():raise ValueError('Prompt is empty')
    cwd = Path(cwd).expanduser().resolve()
    if not cwd.is_dir():raise ValueError('Not a directory: %s' % cwd)
    chosen = targets(spec, env, which)
    if not chosen:raise ValueError('No target agents (all: none installed besides the caller)')
    me = caller(env)
    embedded = context(cwd, files, diff)
    parent = Path(base or state_dir()/'ask'); parent.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'), dir=parent))
    (run_dir/'prompt.md').write_text(prompt+('\n\n'+embedded if embedded else '')+'\n')
    child = child_env(env, depth+1)
    jobs = {}
    with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
        for agent in chosen:
            executable = which(agent)
            if not executable:continue
            full = '\n\n'.join(x for x in (preamble(agent, cwd, me), prompt, embedded) if x)
            jobs[agent] = pool.submit(run_one, agent, executable, full, cwd, run_dir, child, timeout,
                                      (extra or {}).get(agent, ()))
    results = []
    for agent in chosen:
        if agent not in jobs:results.append({'target': agent, 'status': 'missing'});continue
        try:results.append(jobs[agent].result())
        except (OSError, subprocess.SubprocessError) as exc:results.append({'target': agent, 'status': 'error', 'error': str(exc)})
    return {'run': str(run_dir), 'cwd': str(cwd), 'caller': me, 'results': results}


def _extra(values):
    out = {}
    for item in values:
        name, sep, args = item.partition('=')
        if not sep:raise ValueError('--target-args needs TARGET="ARGS"')
        out.setdefault(resolve(name), []).extend(shlex.split(args))
    return out


def command(argv=None):
    if argv and argv[0] == 'check':
        p = argparse.ArgumentParser(prog='agents agent ask check', description='Check each adapter against the installed CLI help')
        p.add_argument('targets', nargs='?', default='all', help='comma-separated agents (default: all supported)')
        args = p.parse_args(argv[1:]); report = []
        for agent in AGENTS if args.targets == 'all' else targets(args.targets):
            executable = agent_executable(agent)
            missing = check(agent, executable) if executable else None
            report.append({'target': agent, 'executable': executable,
                           'status': 'missing' if executable is None else 'unsupported' if missing else 'ok',
                           **({'missing_flags': missing} if missing else {})})
        for item in report:
            print('  %-12s %-11s %s' % (item['target'], item['status'], ', '.join(item.get('missing_flags', [])) or item['executable'] or ''))
        return 0 if all(i['status'] != 'unsupported' for i in report) else 1
    p = argparse.ArgumentParser(prog='agents agent ask', description=__doc__.split('\n\n')[0],
                                epilog='Agents: '+', '.join(AGENTS)+' (aliases: kiro, cursor, antigravity). '
                                       '`agents agent ask check [TARGETS]` verifies the adapters.')
    p.add_argument('targets', help='comma-separated agents, or all (installed, without the calling agent)')
    p.add_argument('prompt', nargs='?', help='the request; - or omitted reads stdin')
    p.add_argument('--cwd', default='.', help='directory the targets work in (default: current)')
    p.add_argument('--file', action='append', default=[], help='embed a file in the prompt (repeatable)')
    p.add_argument('--diff', help='embed a git diff: unstaged, staged, or a ref')
    p.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help='seconds per target (default %d)' % DEFAULT_TIMEOUT)
    p.add_argument('--target-args', action='append', default=[], metavar='TARGET="ARGS"',
                   help='extra CLI arguments for one target, e.g. codex="-c model_reasoning_effort=xhigh"')
    p.add_argument('--text', action='store_true', help='print the answers after the JSON line')
    args = p.parse_args(argv)
    prompt = args.prompt
    if prompt in (None, '-'):
        import sys
        prompt = sys.stdin.read()
    report = ask(args.targets, prompt, args.cwd, args.file, args.diff, args.timeout, _extra(args.target_args))
    print(json.dumps(report, ensure_ascii=False))
    if args.text:
        for item in report['results']:
            if Path(item.get('answer', '')).is_file():
                print('\n===== %s (%s) =====\n%s' % (item['target'], item['status'], Path(item['answer']).read_text()))
    return 0 if all(r['status'] == 'ok' for r in report['results']) else 1
