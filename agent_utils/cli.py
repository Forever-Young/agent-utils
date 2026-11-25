"""Dependency-free CLI with optional fzf fuzzy menus. All mutations are explicit."""
import argparse
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from .core.agents import AGENT_NAMES, AGENTS, agent_executable  # noqa: F401 (re-exported)
from .core.files import atomic_write  # noqa: F401
from .core.paths import config_path  # noqa: F401


def run(argv, **kwargs):
    return subprocess.check_output(argv, text=True, **kwargs).strip()


def load(path):
    data = json.loads(Path(path).read_text())
    return validate(data)


def check_agent_order(order):
    if order is None:return
    if not isinstance(order,list) or any(x!='*' and x not in AGENTS for x in order) or len(set(order))!=len(order):
        raise ValueError("agent_order must be a unique list of supported providers and optional '*' (the rest)")


def check_zellij(value):
    """Optional `zellij` key on a window or project: layout, session, logs_command."""
    if value is None:return
    if not isinstance(value,dict) or set(value)-{'layout','session','logs_command'}:
        raise ValueError('zellij must be an object with layout, session and/or logs_command')
    import re
    if 'layout' in value and not (isinstance(value['layout'],str) and re.match(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}$',value['layout'])):
        raise ValueError('zellij.layout must be a layout name (letters, digits, . _ -)')
    if 'session' in value and not (isinstance(value['session'],str) and re.match(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}$',value['session'])):
        raise ValueError('zellij.session must be shared, project or a session name')
    logs=value.get('logs_command')
    if logs is not None and (not isinstance(logs,list) or not logs or any(not isinstance(x,str) or '\x00' in x for x in logs)):
        raise ValueError('zellij.logs_command must be a nonempty array of strings')


def validate(data):
    if not isinstance(data,dict): raise ValueError('Configuration must be an object')
    if data.get('version') != 1 or not isinstance(data.get('windows'), list):
        raise ValueError('Expected version 1 and windows array')
    if data.get('new_session_position','default') not in ('default','top'):
        raise ValueError('new_session_position must be default or top')
    for holder in [data]+[p for w in data['windows'] if isinstance(w,dict) for p in w.get('projects',[]) if isinstance(p,dict)]:
        name=holder.get('coordinator_name','Main')
        if not isinstance(name,str) or not name.strip() or len(name)>80:
            raise ValueError('coordinator_name must be a non-empty string up to 80 characters')
    zellij=data.get('zellij',{})
    if not isinstance(zellij,dict):raise ValueError('zellij must be an object')
    for key in ('config_path','ssh_config_path'):
        if key in zellij and (not isinstance(zellij[key],str) or '\x00' in zellij[key]):
            raise ValueError('zellij.'+key+' must be a path string')
    session=zellij.get('session','agents')
    if not isinstance(session,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',session) or session.startswith('-'):
        raise ValueError('zellij.session must be a session name (letters, digits, dot, dash, underscore)')
    providers=data.get('providers',{})
    if not isinstance(providers,dict):raise ValueError('providers must be an object')
    if not isinstance(providers.get('auto_detect',True),bool):raise ValueError('providers.auto_detect must be boolean')
    for field in ('hidden','enabled'):
        values=providers.get(field,[])
        if not isinstance(values,list) or any(not isinstance(x,str) or x not in AGENTS for x in values) or len(set(values))!=len(values):
            raise ValueError('providers.'+field+' must be a unique list of supported providers')
    launch_args=providers.get('launch_args',{})
    if not isinstance(launch_args,dict) or any(agent not in AGENTS or not isinstance(values,list) or any(not isinstance(value,str) or '\x00' in value for value in values) for agent,values in launch_args.items()):
        raise ValueError('providers.launch_args must map supported providers to string arrays')
    ids, names = set(), set()
    for window in data['windows']:
        if not isinstance(window,dict): raise ValueError('Window must be an object')
        if not window.get('name') or window['name'] in names:
            raise ValueError('Window names must be unique and nonempty')
        names.add(window['name'])
        if window.get('default_agent') not in AGENTS:
            raise ValueError('Unsupported default agent')
        check_agent_order(window.get('agent_order'))
        check_zellij(window.get('zellij'))
        if not isinstance(window.get('projects',[]),list): raise ValueError('projects must be an array')
        project_names=set()
        for project in window.get('projects', []):
            if not isinstance(project,dict): raise ValueError('Project must be an object')
            if project.get('name') in project_names: raise ValueError('Workspace names must be unique within a window')
            project_names.add(project.get('name'))
            if not project.get('id') or project['id'] in ids:
                raise ValueError('Project IDs must be unique and nonempty')
            ids.add(project['id'])
            if not project.get('name') or not isinstance(project.get('roots'),list) or not project['roots'] or not all(isinstance(r,str) and r for r in project['roots']):
                raise ValueError('Project needs name and nonempty string roots')
            check_agent_order(project.get('agent_order'))
            check_zellij(project.get('zellij'))
            if project.get('default_agent', window['default_agent']) not in AGENTS:
                raise ValueError('Unsupported project default agent')
    if not isinstance(data.get('remotes',[]),list):raise ValueError('remotes must be an array')
    for remote in data.get('remotes',[]):
        from .agterm.remote import valid_host
        if not isinstance(remote,dict):raise ValueError('Remote must be an object')
        valid_host(remote.get('host'))
    return data


def with_provider_launch_args(project,data):
    result=dict(project)
    result.pop('provider_launch_args',None)
    defaults=data.get('providers',{}).get('launch_args',{})
    if defaults:result['provider_launch_args']=defaults
    return result


def projects(data):
    return [(w,with_provider_launch_args(p,data)) for w in data['windows'] for p in w.get('projects',[]) if p.get('enabled',True)]


class _Sep:
    def __repr__(self):return 'SEP'
SEP=_Sep()
SEPARATOR=('─'*28,SEP)


class _Back:
    def __repr__(self):return 'BACK'
BACK=_Back()


def pick(prompt, entries, back=False):
    """Opaque numeric prefix ensures titles are never interpreted as commands.

    With back=True a final "Back" entry is appended and BACK is returned for it;
    None still means cancel."""
    if not entries:
        raise ValueError('No matching entries')
    if back:entries=list(entries)+[('← Back',BACK)]
    if all(v is SEP for _,v in entries):raise ValueError('No matching entries')
    mode=os.environ.get('AGTERM_UTILS_MENU','auto')
    if mode not in ('auto','textual','fzf','plain'):raise ValueError('AGTERM_UTILS_MENU must be auto, textual, fzf or plain')
    if mode in ('auto','textual') and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            from .agterm.tui import select
        except ImportError:
            if mode=='textual':raise ValueError('Textual is not installed; run scripts/install.py --with-tui --apply')
        else:return select(prompt,entries)
    while True:
        result=_pick_text(prompt,entries,mode)
        if result is not SEP:return result


def _pick_text(prompt,entries,mode):
    labels = ['%d\t%s' % (i, ''.join(c if c.isprintable() else ' ' for c in label)) for i,(label,_) in enumerate(entries)]
    if mode!='plain' and shutil.which('fzf'):
        proc = subprocess.run(['fzf','--prompt',prompt+' > ','--delimiter','\t','--with-nth','2..'],input='\n'.join(labels),text=True,stdout=subprocess.PIPE)
        if proc.returncode: return None
        return entries[int(proc.stdout.split('\t',1)[0])][1]
    print(prompt)
    for i,(label,value) in enumerate(entries): print(label if value is SEP else '%d. %s' % (i+1,label))
    answer=input('Number, or Enter to cancel: ').strip()
    if not answer: return None
    n=int(answer)-1
    if n<0 or n>=len(entries): raise ValueError('Invalid selection')
    return entries[n][1]


def agent_argv(project, agent, resume=None, prompt=None, coordinator=False, resume_picker=False):
    if agent not in AGENTS:raise ValueError('Unsupported agent: '+agent)
    roots=[str(Path(r).expanduser().absolute()) for r in project['roots']]
    if agent in ('kiro-cli','opencode','pi') and len(roots)>1:
        raise ValueError(agent+' launches require one root; configure a common parent directory')
    argv=[agent_executable(agent) or agent]
    if agent=='kiro-cli':
        argv += ['chat']
        if resume:argv += ['--resume-id',resume]
        elif resume_picker:argv += ['--resume-picker']
    elif agent=='opencode':
        if resume:argv += ['--session',resume]
        elif resume_picker:argv += ['--continue']
    elif agent=='cursor-agent':
        if resume:argv += ['--resume',resume]
        elif resume_picker:argv += ['--resume']
    elif agent=='pi':
        if resume:argv += ['--session',resume]
        elif resume_picker:argv += ['--resume']
    elif agent=='kimi':
        if resume:argv += ['--session',resume]
        elif resume_picker:argv += ['--session']
    elif agent in ('qwen','vibe'):
        if resume:argv += ['--resume',resume]
        elif resume_picker:argv += ['--resume']
    elif agent=='copilot':
        if resume:argv += ['--resume='+resume]
        elif resume_picker:argv += ['--resume']
    elif agent=='agy':
        if resume:argv += ['--conversation',resume]
        elif resume_picker:argv += ['--continue']
    elif agent=='codex' and (resume or resume_picker):
        argv += ['resume']+([resume] if resume else [])
    elif agent=='claude' and (resume or resume_picker):
        argv += ['--resume']+([resume] if resume else [])
    if agent == 'claude':
        argv += ['--name', project.get('session_title',project['name'])]
    for root in roots[1:]:
        argv += ['--add-dir',root]
    mapping=project.get('agent_args',{})
    if not isinstance(mapping,dict): raise ValueError('agent_args must be an object')
    extra=mapping.get(agent,project.get('provider_launch_args',{}).get(agent,[]))
    if not isinstance(extra,list) or not all(isinstance(x,str) for x in extra):
        raise ValueError('agent_args must contain string arrays')
    argv += extra
    if coordinator and agent=='codex':
        from .agterm.coordinator_agents import codex_args
        argv += codex_args(COORDINATOR)
    elif coordinator and agent=='kiro-cli':
        from .agterm.coordinator_agents import KIRO_AGENT
        argv += ['--agent',KIRO_AGENT]
    elif coordinator:
        if agent!='claude': raise ValueError('Coordinator supports Claude, Codex and Kiro')
        gate=shlex.join([sys.executable,str(Path(__file__).with_name('agterm')/'coordinator_gate.py')])
        settings={'hooks':{'PreToolUse':[{'matcher':'*','hooks':[{'type':'command','command':gate}]}]}}
        argv += ['--model', 'sonnet', '--system-prompt-snapshot', 'off',
                 '--append-system-prompt', COORDINATOR, '--settings', json.dumps(settings)]
    if prompt:
        if agent=='opencode':argv += ['--prompt',prompt]
        elif agent in ('agy','qwen'):argv += ['--prompt-interactive',prompt]
        elif agent=='copilot':argv += ['-i',prompt]
        elif agent=='kimi':raise ValueError('Kimi interactive launches do not support an initial prompt; enter it in the TUI')
        elif agent=='vibe':argv += ['--',prompt]
        elif agent=='cursor-agent':argv += ['--',prompt]
        else:argv.append(prompt)
    return argv, roots[0]


COORDINATOR = '''You are the coordinator of this project. Your main feature is opening and reusing worker sessions: you turn each request into a message to the right worker session, open that session when it does not exist, and track what comes back. You are a dispatcher and nothing else.

THE RULE: you do not do the work yourself, not even when it is small, quick, or "just one look". No reading tickets in depth, no investigating code, no searching, no writing plans, no editing files, no creating worktrees, no commits, no tests, no Sentry, no logs, no progress checks, no web lookups, no MCP tools other than Plannotator. If you notice you are about to read a file, run a search or open a ticket, stop and dispatch that step to a worker instead. A tool gate denies edits, investigation tools and non-dispatch shell commands in this session; a denial is not a puzzle to route around (no clever shell forms, no different tool for the same job), it means: dispatch it to a worker. Your only permitted actions are: open or message worker sessions with `agents agterm ...`, read project rules and tasks/ notes, write tasks/ notes, use the clipboard, and report to the human.

Read AGENTS.md and CLAUDE.md at the project root only to learn the project rules you must pass on, and the rules of each affected repository when you name it in a worker's message. Never invent organizational rules, never approve another worker's actions, and never assume an idle process means the task is complete. Do not launch demonstration work. Report blockers and ask for human decisions.

For every request (a feature, a ticket, PR comments, a follow-up on earlier work):

1. Identify the task using the ticket key, PR URL or branch, with its project. Read tasks/<KEY>.md when it exists to recover previously recorded identities. If no stable identity can be determined, ask the human once.
2. Use a focused lookup: `agents agterm session list --project <id> --task <KEY>`. Alternatively use --pr <URL>, --branch <NAME>, --conversation <ID>, or --record <ID>. These return compact JSON with exact IDs, aliases, observed state and alive (true, false, or null for unknown). Exact identity lookups include hidden and archived records. Multiple filters on list are combined. Do not scan the full registry or match workers by title alone. A terminal row or saved status is not proof that the provider is running. A live detached worker must be reattached; do not start a second writer. Comments on a PR go to the worker that built the feature. Never run two workers for the same task.
3. Dispatch with one command: `agents agterm ensure --project <id> --window <name> --agent <worker agent> --task <KEY> --name "<KEY> short description" --prompt "<request text>"`. Use --pr or --branch in place of --task for a linked worker, or --record to resolve an ambiguous match. ensure reuses the live worker, otherwise resumes its registered conversation only when it can exclude a live writer, otherwise creates a new worker. It returns one JSON line with action (reused/resumed/created), record/conversation/terminal IDs, observed state and delivery status. Unknown identity, blocked input, detached workers and conflicting matches are blockers; do not bypass them with new. delivery=submitted means terminal input was submitted, not that the worker has processed it. Take project IDs and configured window names from `agents agterm list`; never pass dry-run placeholders as window IDs. Use the project's usual worker agent (the default shown by `agents agterm list`) unless the human names another. When the human says to hand a task to a specific agent (it fits better, or another agent is at its limits), pass that agent in --agent. Follow-ups reach live Claude, Codex and Kiro workers; a worker that is not running is not resumed for Codex or Kiro, and a task that already has a worker on another agent is refused. Report such refusals to the human instead of working around them, and never start a second writer for the same task. Do not call agtermctl directly to launch workers. Do not pass a model unless the human asks. --root sets launch roots and the first starting directory; --cwd overrides only the starting directory. Existing workers retain their saved roots and cwd.
4. The first message to a worker contains: the human's request verbatim, the ticket or PR link, the affected repositories, and the standing rules it must follow:
   - read AGENTS.md and the rules of each repository it edits;
   - use a task worktree for every edited repository, from the latest main/master unless the human said otherwise; inspect dirty changes first and never move unrelated work;
   - put temporary files in tmp/;
   - follow the human and repository rules for commits and pushes; do not invent an approval requirement;
   - follow any supplied Jira workflow and approval rules;
   - for complex work, the worker investigates and uses the planning/review workflow required by the human or repository;
   - use the requested peer-review workflow with bounded scope and runs;
   - report back with changed files, test and lint output, and open questions.
   Do not pre-digest the task or answer questions the worker should answer.
5. Check the JSON state before reporting the worker as running. If launch identity is not ready, repeat the focused lookup; do not create another record. Keep the exact record ID, conversation ID, terminal ID, repository/worktree paths and observed state in tasks/<KEY>.md. Bind discovered links with `agents agterm task bind --record <ID> --pr <URL> --branch <NAME> --alias <KEY2> --repo <PATH>` (omit unused flags; --repo and --alias repeat). Ask the worker to report repositories it actually edited; declared repo links are not proof of edits. When a provisional task gets its real ticket key, use `agents agterm task rename --record <ID> --to-task <KEY>`; the old key remains an alias.
6. Send every follow-up, including PR comments and human decisions, through ensure using the existing task/link or exact record ID. Pass the human's decisions (Plannotator or Inbox answers) unchanged. Do not infer task completion from idle state or an exited process; require the worker's result. Follow-up text is stored in an external message file; ensure submits a short prompt pointing the verified Claude process to that file without modifying the clipboard. It cannot atomically guard against a person typing or the terminal process changing after verification; report delivery failures instead of retrying with a new worker.
7. Report to the human briefly: the ticket link, which worker (reused or new) and its state, then only what needs their attention (a blocker, a refusal, a decision). A task is complete only when the worker reports its result and the output backs it up.

Reply style: short, plain, about the work. Do not recite IDs (record, conversation, terminal), file paths, the instructions you gave the worker, what you did not read, or how ensure works; they are in tasks/<KEY>.md. Do not repeat the human's own decisions back to them. No bullet lists unless there are several separate items. One marked question per decision, only when a decision is really needed. Example: "Started a worker for CBS24-458 (link); it is running and has taken your Inbox answers. I'll relay its result.".

Clipboard access: use `pbpaste` to read, `pbcopy < tasks/prompt.txt` or `pbcopy < tmp/draft.txt` to copy a file, or `printf '%s' 'text' | pbcopy` for short text. Use one simple command at a time; the clipboard pipe and scoped input redirect are the only shell-operator exceptions. The --prompt value is request text, not a filename.

Use the human's own wording for ticket keys, PRs and Sentry issues as clickable links, as AGENTS.md requires.'''


class Agterm:
    def __init__(self, dry=False, session_position='default'):
        self.dry=dry;self.session_position=session_position
    def call(self,*args,json_result=False):
        argv=[shutil.which('agtermctl') or 'agtermctl',*args]
        sock=os.environ.get('AGTERM_SOCKET') or os.environ.get('AGT_SOCKET')
        if sock: argv+=['--socket',sock]
        if json_result: argv+=['--json']
        if self.dry:
            print(shlex.join(argv)); return None
        result=run(argv)
        if json_result:
            obj=json.loads(result)
            return obj.get('result',obj)
        return result
    def window(self,name):
        if self.dry:
            try:
                items=Agterm().call('window','list',json_result=True)
                if isinstance(items,dict):items=items.get('windows',[])
                matches=[w for w in items if w.get('name')==name and not w.get('closed')]
                if len(matches)>1:raise ValueError('Ambiguous window name: '+name)
                if matches:return matches[0]['id']
            except (OSError,subprocess.SubprocessError,json.JSONDecodeError):pass
            return '<unresolved-window:'+name+'; allocated on apply>'
        items=self.call('window','list',json_result=True)
        if isinstance(items,dict): items=items.get('windows',[])
        matches=[w for w in items if w.get('name')==name and not w.get('closed')]
        if len(matches)>1: raise ValueError('Ambiguous window name: '+name)
        return matches[0]['id'] if matches else self.call('window','new',name)
    def launch(self,window,project,argv,cwd):
        wid=self.window(window['name'])
        terminal=self.call('session','new','--window',wid,
                  '--workspace-name',project['name'],'--create-workspace',
                  '--cwd',cwd,'--name',project.get('session_title',project['name']),
                  '--command',shlex.join(['env','AGTERM_UTILS_PLAIN_SHELL=1',*argv]),'--wait')
        from .agterm.placement import place_once
        place_once(self,wid,terminal or ('<new-session>' if self.dry else None),self.session_position)
        return terminal


def main(argv=None):
    parser=argparse.ArgumentParser(prog='agents agterm',description='Project launcher for agterm; fzf enables fuzzy menus')
    parser.add_argument('--config',default=str(config_path()))
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('command',choices=['list','menu','new','ensure','resume','coordinator','bootstrap','doctor','worktree','validate','startup','restore','project','session','task','dashboard','history','gui','settings','inbox','cheatsheet'])
    parser.add_argument('operation',nargs='?',choices=['add','edit','enable','disable','move','order','list','focus','resume','hide','unhide','archive','unarchive','forget','bind','status','finish','provider-archive','provider-unarchive','provider-delete','rename','sync','snapshot','plan','pin','doctor','threads','dispatch','launches','adopt','subject','reopen','author','withdraw','move','relay'])
    parser.add_argument('--name');parser.add_argument('--root',action='append',default=[])
    parser.add_argument('--position',type=int);parser.add_argument('--send',action='store_true')
    parser.add_argument('--project');parser.add_argument('--window');parser.add_argument('--agent',choices=AGENTS)
    parser.add_argument('--alternate',action='store_true');parser.add_argument('--session')
    parser.add_argument('--task');parser.add_argument('--description',default='');parser.add_argument('--prompt')
    parser.add_argument('--submenu',choices=['agents','history','actions'],default='actions')
    parser.add_argument('--repo',action='append',default=[]);parser.add_argument('--destination');parser.add_argument('--base',default='HEAD')
    parser.add_argument('--record');parser.add_argument('--target',help='agterm session id for restore pin');parser.add_argument('--conversation')
    parser.add_argument('--pr');parser.add_argument('--branch')
    parser.add_argument('--alias',action='append',default=[]);parser.add_argument('--to-task')
    parser.add_argument('--compact',action='store_true');parser.add_argument('--live',action='store_true')
    parser.add_argument('--refresh',action='store_true');parser.add_argument('--cwd')
    parser.add_argument('--all',action='store_true');parser.add_argument('--yes',action='store_true')
    parser.add_argument('--in-place',action='store_true');parser.add_argument('--backend-stopped',action='store_true')
    parser.add_argument('--state',choices=['idle','running','active','blocked','completed','failed'])
    parser.add_argument('--result');parser.add_argument('--failed',action='store_true')
    parser.add_argument('--limit',type=int,default=50);parser.add_argument('--adopt')
    parser.add_argument('--provider-archived',action='store_true')
    parser.add_argument('--launch-args',help='Default arguments for --agent; use --launch-args="--flag value"; empty clears')
    parser.add_argument('--agent-order',help="Comma-separated provider order for --window/--project; '*' = the rest; empty clears")
    parser.add_argument('--zellij-layout',help='Zellij tab layout for --window/--project (preset or own file); empty clears')
    parser.add_argument('--zellij-session',help='Zellij session for --window/--project: shared, project or a name; empty clears')
    parser.add_argument('--auto-detect',choices=['on','off'])
    parser.add_argument('--hide-agent',choices=AGENTS,action='append',default=[])
    parser.add_argument('--show-agent',choices=AGENTS,action='append',default=[])
    parser.add_argument('--enabled-agent',choices=AGENTS,action='append',default=None)
    parser.add_argument('--thread',action='append',default=[],help='Plannotator Inbox thread id (repeatable)')
    parser.add_argument('--launch',action='append',default=[],help='inbox adopt: Plannotator Inbox launch id (repeatable)')
    parser.add_argument('--answered',action='store_true',help='inbox: threads whose last message is the person\'s')
    parser.add_argument('--force',action='store_true');parser.add_argument('--fix',action='store_true')
    parser.add_argument('--subject');parser.add_argument('--message');parser.add_argument('--question');parser.add_argument('--author-host')
    parser.add_argument('--clear',action='store_true',help='inbox rename: remove the thread title so the subject shows')
    parser.add_argument('--once',action='store_true',help='inbox relay: one pass, then exit')
    parser.add_argument('--interval',type=float,default=8,help='inbox relay: seconds between passes')
    parser.add_argument('--idle-seconds',type=float,default=10,help='inbox relay: how long a Kiro session must be idle first')
    parser.add_argument('--output',help='cheatsheet: file to write (default: state dir, never the repository)')
    parser.add_argument('--format',choices=['pdf','md','html'],help='cheatsheet: pdf needs reportlab; default pdf when present, else html')
    parser.add_argument('--open',action='store_true',help='cheatsheet: open the file afterwards')
    args=parser.parse_args(argv)
    if args.command=='cheatsheet':
        from .agterm.cheatsheet import command as cheatsheet_command
        return cheatsheet_command(args)
    if args.command=='inbox':
        from .plannotator import inbox
        if args.operation=='relay':
            from .plannotator import relay
            return relay.relay(args)
        if args.operation=='doctor':return inbox.doctor(args)
        if args.operation in ('rename','subject','reopen','author','withdraw','move'):
            from .plannotator import state
            if len(args.thread)!=1:parser.error('inbox %s needs exactly one --thread'%args.operation)
            apply=args.yes and not args.dry_run
            if args.operation=='rename':
                if args.clear==(args.name is not None):parser.error('inbox rename needs --name TITLE or --clear')
                report=state.rename(args.thread[0],None if args.clear else args.name,apply=apply)
            else:
                recipe={'rename':'title'}.get(args.operation,args.operation)
                options={'subject':{'subject':args.subject},'author':{'message':args.message,'name':args.name,'host':args.author_host},
                         'withdraw':{'question':args.question},'move':{'destination':args.destination},'reopen':{}}[args.operation]
                report=state.edit(recipe,args.thread[0],apply=apply,**options)
            print(json.dumps(report,indent=2,ensure_ascii=False,default=str))
            return 0 if report.get('doctor',{}).get('healthy',True) else 1
        if args.operation not in ('threads','dispatch','launches','adopt'):parser.error('inbox requires doctor, threads, dispatch, launches, adopt or an edit (rename, subject, reopen, author, withdraw, move)')
        data=load(args.config) if Path(args.config).exists() or args.operation in ('dispatch','adopt') else None
        pairs=projects(data) if data else []
        if args.operation=='threads':inbox.list_threads(args,pairs);return
        if args.operation=='launches':inbox.list_launches(args,pairs);return
        if args.operation=='adopt':inbox.adopt(args,Agterm(args.dry_run,data.get('new_session_position','default')),pairs);return
        inbox.dispatch(args,Agterm(args.dry_run,data.get('new_session_position','default')),pairs);return
    if args.command=='settings':
        from .agterm.configuration import settings_command
        return settings_command(args)

    if args.command=='dashboard':
        from .agterm.dashboard import dashboard
        if args.dry_run:print('Would show the session dashboard');return
        return dashboard(args,Agterm())
    if args.command=='session':
        from .agterm.sessions import command
        position=load(args.config).get('new_session_position','default') if args.operation=='resume' and Path(args.config).exists() else 'default'
        return command(args,Agterm(args.dry_run,position))
    if args.command=='restore':
        from .agterm.restore import command as restore_command
        return restore_command(args,Agterm(args.dry_run))
    if args.command=='project':
        from .agterm.configuration import configure
        if not args.operation:parser.error('project requires an operation')
        return configure(args)
    if args.command=='doctor':
        print(json.dumps({'executables':{x:agent_executable(x) for x in ['agtermctl',*AGENTS,'fzf']},'config':args.config},indent=2));return
    if args.command=='startup':
        from .agterm.startup import startup
        return startup(args.config)
    if args.command=='task' and args.operation in ('bind','rename'):
        from .agterm.workers import link_task
        return link_task(args,Agterm(args.dry_run))
    data=load(args.config);pairs=projects(data)
    if args.command=='validate': print('Configuration valid');return
    if args.command=='list':
        print(json.dumps([{'window':w['name'],**p} for w,p in pairs],indent=2));return
    ag=Agterm(args.dry_run,data.get('new_session_position','default'))
    if args.command=='bootstrap':
        for w in data['windows']:
            selected_projects=[p for p in w.get('projects',[]) if p.get('enabled',True) and (not args.project or p['id']==args.project)]
            if (args.window and w['name']!=args.window) or (args.project and not selected_projects):continue
            wid=ag.window(w['name'])
            tree=ag.call('tree','--window',wid,json_result=True) if not args.dry_run else {}
            tree=(tree or {}).get('tree',tree or {})
            if data.get('new_session_position')=='top' and not args.dry_run:
                from .agterm.placement import remember_existing
                remember_existing(tree)
            existing={x['name'] for x in tree.get('workspaces',[])}
            for p in selected_projects:
                if p['name'] not in existing:
                    ag.call('workspace','new',p['name'],'--window',wid)
        return
    pairs=[(w,p) for w,p in pairs if (not args.project or p['id']==args.project) and (not args.window or w['name']==args.window)]
    if args.command=='ensure' and len(pairs)!=1:raise ValueError('ensure requires one exact --project and --window selection')
    selected=pairs[0] if len(pairs)==1 else pick('Project',[(w['name']+' / '+p['name'],(w,p)) for w,p in pairs])
    if selected is None:return
    window,project=selected;project=dict(project)
    if args.command=='history':
        from .agterm.history import discover
        args.selected_window=window['name']
        if args.dry_run and not args.adopt:print('Would discover project-scoped provider metadata');return
        return discover(project,args)
    if args.command=='gui':
        agent=args.agent or project.get('default_agent',window['default_agent'])
        if agent not in ('claude','codex'):raise ValueError('GUI handoff is not supported for '+agent)
        cwd=str(Path(project['roots'][0]).expanduser().resolve())
        command=['codex','app',cwd] if agent=='codex' else ['claude','--desktop']+(['--resume',args.session] if args.session else [])
        if args.dry_run:print(shlex.join(command));return
        return subprocess.call(command,cwd=cwd)
    if args.command=='worktree':
        if not args.task or not args.destination or not args.repo: raise ValueError('worktree requires --task --destination --repo (repeatable)')
        if not all(c.isalnum() or c in '-_/' for c in args.task) or args.task.startswith('-'):raise ValueError('Invalid task branch')
        dest=Path(args.destination).expanduser()
        targets=[(Path(r).expanduser().resolve(),dest/Path(r).name) for r in args.repo]
        if len({str(t) for _,t in targets})!=len(targets): raise ValueError('Repository basenames collide')
        for repo,target in targets:
            if target.exists():raise ValueError('Target already exists: '+str(target))
            run(['git','-C',str(repo),'rev-parse','--verify',args.base])
            if run(['git','-C',str(repo),'status','--porcelain']): raise ValueError('Dirty repository: '+str(repo))
        for repo,target in targets:
            cmd=['git','-C',str(repo),'worktree','add','-b',args.task,str(target),args.base]
            if args.dry_run: print(shlex.join(cmd))
            else: target.parent.mkdir(parents=True,exist_ok=True);subprocess.run(cmd,check=True)
        return
    agent=args.agent or project.get('default_agent',window['default_agent'])
    if args.alternate:agent='codex' if agent=='claude' else 'claude'
    if args.command=='menu':
        from .agterm.startup import pick_provider,choose_session
        while True:
            agent=pick_provider('Provider',agent,data,args.config,args.dry_run)
            project=with_provider_launch_args(project,load(args.config))
            if agent is None:return
            action=choose_session(project,agent,back=True)
            if action is None:return
            if action is BACK:continue
            args.command,args.session=action
            if args.command=='coordinator':args.agent=agent
            break
    if args.root and args.command in ('new','resume','ensure'):
        project['roots']=[str(Path(r).expanduser().resolve()) for r in args.root]
    if args.name:project['session_title']=args.name
    elif args.task:project['session_title']=(args.task+' '+args.description).strip()
    if args.command=='ensure':
        from .agterm.workers import ensure
        return ensure(args,ag,window,project,agent)
    if args.command=='coordinator':
        project.setdefault('coordinator_name',data.get('coordinator_name','Main'))
        return launch_coordinator(ag,window,project,args)
    from .agterm.sessions import launch,task_project
    if args.command=='task':project=task_project(project,args,args.dry_run)
    return launch(ag,window,project,agent,args)


COORDINATOR_AGENTS=('claude','codex','kiro-cli')


def launch_coordinator(ag,window,project,args):
    """Reuse a live row or an exact conversation. No model calls are made here."""
    from .agterm.state import locked_registry,new_record
    project=dict(project);project['session_title']=project.get('coordinator_name') or 'Main'
    with locked_registry() as registry:
        record=registry['coordinators'].get(project['id'])
        agent=args.agent or (record or {}).get('agent') or 'claude'
        if agent not in COORDINATOR_AGENTS:raise ValueError('Coordinator supports Claude, Codex and Kiro')
        if record and record.get('agent','claude')!=agent:
            raise ValueError('This project\'s coordinator is '+record.get('agent','claude')+'; forget it before starting a '+agent+' coordinator')
        if args.dry_run:
            command,cwd=agent_argv(project,agent,args.session,args.prompt,True)
            if agent=='claude' and not args.session:command[1:1]=['--session-id','<new-uuid>']
            return ag.launch(window,project,command,cwd)
        for root in project['roots']:
            if not Path(root).expanduser().is_dir():raise ValueError('Root unavailable: '+root)
        executable=agent_executable(agent) or agent
        if not shutil.which(executable):raise ValueError(agent+' executable missing')
        wid=ag.window(window['name'])
        tree=ag.call('tree','--window',wid,json_result=True)
        tree=tree.get('tree',tree)
        rows=[s for w in tree.get('workspaces',[]) for s in w.get('sessions',[])]
        if record and any(s['id']==record.get('terminal_id') for s in rows):
            ag.call('session','select','--target',record['terminal_id'],'--window',wid)
            return
        picker=False
        if record and agent=='claude':
            # A surviving agent must not be resumed as a second writer.
            active=json.loads(run([shutil.which('claude'),'agents','--json']))
            if not isinstance(active,list):raise ValueError('Unexpected Claude agent inventory; refusing duplicate risk')
            if any(x.get('sessionId',x.get('session_id',x.get('id')))==record['conversation_id'] for x in active):
                raise ValueError('Coordinator still runs outside its recorded row; attach it before continuing')
        elif record:
            # Codex and Kiro cannot pre-assign a conversation id and have no inventory
            # check here: the provider's own picker chooses the conversation to resume.
            picker=not args.session and not record.get('conversation_id')
        elif args.session:
            record={'conversation_id':args.session,'terminal_id':None,'agent':agent}
        else:
            record=new_record();record['agent']=agent
            if agent!='claude':record['conversation_id']=None
        resume=record['conversation_id'] if (registry['coordinators'].get(project['id']) or args.session) and not picker else None
        if agent=='kiro-cli':
            from .agterm.coordinator_agents import write_kiro_agent
            write_kiro_agent(COORDINATOR)
        command,cwd=agent_argv(project,agent,resume,args.prompt,True,resume_picker=picker)
        if agent=='claude' and not resume:command[1:1]=['--session-id',record['conversation_id']]
        # A failed control call leaves the registry unchanged. Missing transcripts
        # are surfaced by the provider, never replaced with a fresh conversation.
        record['terminal_id']=ag.launch(window,project,command,cwd)
        registry['coordinators'][project['id']]=record


def guarded(function):
    try:
        result=function()
        if isinstance(result,int):sys.exit(result)
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        print('Error: '+str(exc),file=sys.stderr);sys.exit(1)


if __name__=='__main__':
    guarded(main)
