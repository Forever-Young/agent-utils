"""PreToolUse gate for coordinator sessions: the coordinator dispatches, workers work.

Passed to the coordinator process only, so workers never see it: Claude via --settings,
Codex via a per-launch `-c hooks.PreToolUse`, Kiro via a generated custom agent
(`--format kiro`: exit code 2 blocks, the reason goes to stderr). Prompts are advice;
this denies the tool call itself. `normalize` maps each agent's tool names and inputs
onto the Claude shapes that `decide` understands.
"""
import json
import os
from pathlib import Path
import re
import shlex
import sys

BASH_FREE = {'agents', 'agtermctl', 'ps', 'pbcopy', 'pbpaste', 'ls', 'pwd', 'date', 'echo'}
BASH_SCOPED = {'cat', 'head', 'tail'}
GIT_READ = {'status', 'log', 'diff', 'show', 'branch', 'rev-parse', 'remote'}
SCOPED_DIRS = {'tasks', 'tmp'}
PROJECT_RULES = {'AGENTS.md', 'CLAUDE.md'}
HINT = ' Dispatch this to a worker: reuse one or create one with `agents agterm new ... --send`.'


def scoped(path):
    parts = Path(path).parts
    return Path(path).name in PROJECT_RULES or bool(SCOPED_DIRS.intersection(parts))


def check_bash(command):
    # Parse operators separately from quoted prompt text. Shell expansion is never
    # needed for dispatch; reject it even inside quotes rather than evaluating it.
    if '\n' in command:return 'multiple command lines are not allowed'
    if re.search(r'`|\$\(', command):
        return 'substitution is not allowed'
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&<>|')
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    lexer.commenters = ''
    try:words = list(lexer)
    except ValueError:return 'unparseable command'
    if not words:return 'empty command'
    name = Path(words[0]).name
    # The only input redirect is a single clipboard read from a scoped file.
    if len(words) == 3 and name == 'pbcopy' and words[1] == '<':
        path = Path(words[2])
        if '..' not in path.parts and SCOPED_DIRS.intersection(path.parts):return None
        return 'clipboard input must be in tasks/ or tmp/'
    # A small stdin producer is allowed only when its sole consumer is pbcopy.
    if '|' in words:
        split = words.index('|')
        source, sink = words[:split], words[split+1:]
        if sink != ['pbcopy'] or not source:
            return 'only clipboard input pipelines are allowed'
        producer = Path(source[0]).name
        if producer not in {'echo', 'printf', 'cat'}:
            return 'clipboard input requires echo, printf or scoped cat'
        if producer == 'cat' and (len(source) != 2 or
                '..' in Path(source[1]).parts or
                not SCOPED_DIRS.intersection(Path(source[1]).parts)):
            return 'clipboard input must be in tasks/ or tmp/'
        words = source
        name = producer
    if any(w and (set(w) <= set(';&<>|')) for w in words):
        return 'chaining and redirects are not allowed'
    if name in BASH_FREE or name == 'printf' and '|' in command:return None
    if name in BASH_SCOPED and all(scoped(w) for w in words[1:] if not w.startswith('-')) and len(words) > 1:return None
    if name == 'git' and len(words) > 1 and words[1] in GIT_READ:return None
    if name == 'git' and words[1:3] == ['worktree', 'list']:return None
    return name + ' is not on the coordinator allowlist'


PATCH_FILE = re.compile(r'^\*\*\* (?:Add|Update|Delete) File: (.+)$|^\*\*\* Move to: (.+)$', re.M)
WORK_WORDS = ('grep', 'glob', 'web', 'search', 'fetch', 'subagent', 'delegate', 'notebook', 'code', 'introspect')


def normalize(event):
    """Return (tool, input) in Claude naming for Claude, Codex and Kiro events."""
    tool = str(event.get('tool_name', ''))
    args = event.get('tool_input') or {}
    if not isinstance(args, dict):args = {'command': args}
    low = tool.lower()
    if tool.startswith('mcp__') or tool.startswith('@'):return tool, args
    if tool == 'apply_patch' or low in ('fs_write', 'write', 'edit', 'str_replace', 'create'):
        text = args.get('command') or args.get('patch') or args.get('input') or ''
        paths = [a or b for a, b in PATCH_FILE.findall(text if isinstance(text, str) else '')]
        paths += [str(args[k]) for k in ('path', 'file_path') if args.get(k)]
        return 'PatchFiles', {'paths': paths or ['<unknown>']}
    if low in ('fs_read', 'read'):
        ops = args.get('operations')
        paths = [str(o.get('path', '')) for o in ops if isinstance(o, dict)] if isinstance(ops, list) else []
        paths += [str(args[k]) for k in ('path', 'file_path') if args.get(k)]
        return 'ReadFiles', {'paths': paths or ['<unknown>']}
    if low in ('execute_bash', 'bash', 'shell', 'execute_shell', 'exec_command', 'shell_command', 'local_shell') or 'shell' in low:
        cmd = args.get('command') or args.get('cmd') or ''
        if isinstance(cmd, list):cmd = shlex.join(str(c) for c in cmd)
        return 'Bash', {'command': str(cmd)}
    if any(w in low for w in WORK_WORDS):return 'Task', args
    return tool, args


def decide(event):
    tool, args = normalize(event)
    if tool == 'PatchFiles':
        bad = [p for p in args['paths'] if not (scoped(p) and 'tasks' in Path(p).parts and '..' not in Path(p).parts)]
        return 'Edit outside tasks/: ' + ', '.join(bad) if bad else None
    if tool == 'ReadFiles':
        bad = [p for p in args['paths'] if not (scoped(p) and '..' not in Path(p).parts)]
        return 'Read outside AGENTS.md, CLAUDE.md, tasks/ and tmp/' if bad else None
    if tool == 'Bash':
        reason = check_bash(args.get('command', ''))
        return 'Bash: ' + reason if reason else None
    if tool in ('Edit', 'Write'):
        return None if scoped(args.get('file_path', '')) and 'tasks' in Path(args.get('file_path', '')).parts else tool + ' outside tasks/'
    if tool == 'Read':
        return None if scoped(args.get('file_path', '')) else 'Read outside AGENTS.md, CLAUDE.md, tasks/ and tmp/'
    if tool in ('NotebookEdit', 'Skill', 'Agent', 'Task', 'Grep', 'Glob', 'WebFetch', 'WebSearch'):
        return tool + ' is worker work'
    if tool.startswith('mcp__') and not tool.startswith('mcp__plannotator'):
        return 'MCP tool ' + tool + ' (tickets, logs, Sentry) is worker work'
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if os.environ.get('AGTERM_UTILS_ROLE', 'coordinator') != 'coordinator':return 0
    try:event = json.load(sys.stdin)
    except ValueError:return 0
    reason = decide(event)
    if reason and argv[:2] == ['--format', 'kiro']:
        print('Coordinator gate: ' + reason + '.' + HINT, file=sys.stderr)
        return 2
    if reason:
        json.dump({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
                   'permissionDecisionReason': 'Coordinator gate: ' + reason + '.' + HINT}}, sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
