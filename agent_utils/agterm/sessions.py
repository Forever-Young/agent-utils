"""Managed launches and task metadata; provider transcripts stay provider-owned."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid
from .state import locked_registry, state_dir

COLORS={'claude':'orange','codex':'green','kiro-cli':'purple','opencode':'blue','cursor-agent':'blue','pi':'purple','agy':'orange',
        'kimi':'purple','qwen':'purple','copilot':'green','vibe':'orange'}


def now():return datetime.now(timezone.utc).isoformat()


def records():
    with locked_registry() as registry:return dict(registry.setdefault('sessions',{}))


def save(record):
    with locked_registry() as registry:registry.setdefault('sessions',{})[record['id']]=record


def decorate(ag,record):
    from ..cli import AGENT_NAMES
    try:
        target=['--target',record['terminal_id'],'--window',ag.window(record['window'])]
        ag.call('session','tint',COLORS[record['agent']],*target)
        ag.call('session','context',record['title'][:60],*target)
        ag.call('session','info',AGENT_NAMES.get(record['agent'],record['agent'])+' · '+record['id'],*target)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print('Session registered; sidebar decoration failed: '+str(exc))


def launch(ag,window,project,agent,args):
    from .workers import dispatch_lock
    with dispatch_lock():
        return launch_unlocked(ag,window,project,agent,args)


def launch_unlocked(ag,window,project,agent,args):
    from ..cli import agent_argv,agent_executable
    resume=args.session
    if not ag.dry:
        identities=set(getattr(args,'alias',[])) | ({args.task} if getattr(args,'task',None) else set())
        if identities and any(r.get('project') == project['id'] and identities.intersection(
               [r.get('task'), *r.get('aliases',[])]) for r in records().values()):
            raise ValueError('Worker already registered for this task; use ensure to reuse it')
    if agent=='opencode' and args.command=='resume' and not resume:
        if ag.dry:
            print('Would choose an OpenCode session from this project and resume its exact ID');return
        from .history import pick_opencode_session
        resume=pick_opencode_session(project)
        if resume is None:return
    conversation=resume or (str(uuid.uuid4()) if agent=='claude' and args.command!='resume' else None)
    command,cwd=agent_argv(project,agent,resume,args.prompt,resume_picker=args.command=='resume')
    cwd=getattr(args,'cwd',None) or cwd
    if not Path(cwd).expanduser().is_dir() and not ag.dry:raise ValueError('Working directory unavailable: '+cwd)
    cwd=str(Path(cwd).expanduser().resolve())
    if agent=='claude' and args.command!='resume' and not resume:
        command[1:1]=['--session-id',conversation]
    # Codex's shared daemon can inherit the first terminal's hook target. Keep
    # this launch and its hooks in this process, without changing global config.
    if agent=='codex':command.append('--no-daemon') if args.prompt is None else command.insert(len(command)-1,'--no-daemon')
    if not ag.dry:
        for root in project['roots']:
            if not Path(root).expanduser().is_dir():raise ValueError('Root unavailable: '+root)
        if not agent_executable(agent):raise ValueError('Agent executable missing: '+agent)
    record={'id':str(uuid.uuid4()),'project':project['id'],'window':window['name'],
            'project_name':project['name'],'roots':project['roots'],'agent':agent,
            'agent_args':project.get('agent_args',{}),'title':project.get('session_title',project['name']),
            'conversation_id':conversation,'created_at':now(),'updated_at':now(),
            'task':getattr(args,'task',None),'hidden':False,'archived':False,'status':'launching','cwd':cwd,
            'aliases':getattr(args,'alias',[]),'prs':[args.pr.rstrip('/')] if getattr(args,'pr',None) else [],
            'branches':[args.branch] if getattr(args,'branch',None) else [],
            'repos':[str(Path(r).expanduser().resolve()) for r in getattr(args,'repo',[])]}
    from .runner import wrapped
    command=wrapped(record['id'],command)
    if ag.dry:return ag.launch(window,project,command,cwd)
    save(record)
    try:terminal=ag.launch(window,project,command,cwd)
    except BaseException:
        with locked_registry() as registry:
            current=registry['sessions'].get(record['id'],{})
            if not current.get('provider_pid') and not current.get('exited_at'):
                registry['sessions'].pop(record['id'],None)
        raise
    with locked_registry() as registry:
        registry['sessions'][record['id']]['terminal_id']=terminal
        record=dict(registry['sessions'][record['id']])
    print('Managed session: '+record['id'])
    decorate(ag,record)
    from .restore import pin_one
    pin_one(ag,record['id'])
    return record


def live_rows(ag,window=None):
    windows=ag.call('window','list',json_result=True)
    if isinstance(windows,dict):windows=windows.get('windows',[])
    rows=[]
    for w in windows:
        if w.get('closed') or (window and w['name']!=window):continue
        tree=ag.call('tree','--window',w['id'],json_result=True);tree=tree.get('tree',tree)
        for workspace in tree.get('workspaces',[]):
            for row in workspace.get('sessions',[]):
                rows.append({**row,'window_id':w['id'],'window_name':w['name'],'workspace_name':workspace['name']})
    return rows


def command(args,ag):
    if args.operation in ('resume','bind','forget'):
        from .workers import dispatch_lock
        with dispatch_lock():return command_unlocked(args,ag)
    return command_unlocked(args,ag)


def command_unlocked(args,ag):
    items=records()
    if args.operation in ('list',None):
        from .workers import list_workers
        return list_workers(args,ag)
    key=args.record or os.environ.get('AGTERM_UTILS_RECORD')
    if not key or key not in items:raise ValueError('Select an exact --record ID from session list')
    record=items[key]
    if args.project and args.project!=record['project']:raise ValueError('Record belongs to another project')
    if args.operation in ('provider-archive','provider-unarchive','provider-delete'):
        if record['agent']!='codex':raise ValueError('Provider archive/delete is only supported for Codex; utility archive remains available')
        if not record.get('conversation_id'):raise ValueError('Bind the exact provider conversation ID first')
        conversation=str(uuid.UUID(record['conversation_id']))
        action=args.operation.removeprefix('provider-')
        if action!='unarchive' and (not args.yes or not args.backend_stopped):
            raise ValueError('Provider archive/delete requires --yes --backend-stopped')
        if not ag.dry and action!='unarchive' and any(r['id']==record.get('terminal_id') for r in live_rows(ag)):
            raise ValueError('Recorded row is still open; close/stop its provider before changing provider history')
        argv=['codex',action,conversation]+(['--force'] if action=='delete' else [])
        if ag.dry:print(shlex.join(argv));return
        subprocess.run(argv,check=True,cwd=str(Path(record['roots'][0]).expanduser()))
        record['provider_archived']=action=='archive'
        if action=='delete':record['provider_deleted']=True;record['archived']=True
        record['updated_at']=now();save(record);return
    if args.operation=='focus':
        live=next((r for r in live_rows(ag) if r['id']==record['terminal_id']),None)
        if not live:raise ValueError('Terminal is not open; use session resume')
        return ag.call('session','select','--target',live['id'],'--window',live['window_id'])
    if args.operation=='resume':
        if ag.dry:print('Would focus existing row or resume '+str(record['conversation_id']));return
        live=next((r for r in live_rows(ag) if r['id']==record['terminal_id']),None)
        if live:return ag.call('session','select','--target',live['id'],'--window',live['window_id'])
        if not record.get('conversation_id'):raise ValueError('Conversation ID not bound; use provider resume picker or session bind with its actual ID')
        # A vanished row does not prove its backend exited. Require deliberate
        # reattachment, never silently open a second writer.
        if not args.backend_stopped:raise ValueError('Confirm the previous provider process ended using --backend-stopped, or reattach its Live session')
        from ..cli import agent_argv,load,with_provider_launch_args
        project={'id':record['project'],'name':record['project_name'],'roots':record['roots'],
                 'session_title':record['title'],'agent_args':record.get('agent_args',{})}
        if Path(args.config).exists():project=with_provider_launch_args(project,load(args.config))
        argv,cwd=agent_argv(project,record['agent'],record['conversation_id'])
        if record['agent']=='codex':argv+=['--no-daemon']
        for root in record['roots']:
            if not Path(root).expanduser().is_dir():raise ValueError('Saved root unavailable: '+root)
        from .runner import wrapped
        for field in ('provider_pid','provider_birth','exit_code','exited_at'):record.pop(field,None)
        record.update(hidden=False,archived=False,status='launching',updated_at=now());save(record)
        terminal=ag.launch({'name':record['window']},project,wrapped(key,argv),record.get('cwd',cwd))
        with locked_registry() as registry:
            registry['sessions'][key]['terminal_id']=terminal
            record=dict(registry['sessions'][key])
        decorate(ag,record)
        from .restore import pin_one
        pin_one(ag,key);return
    if args.operation in ('hide','unhide','archive','unarchive'):
        field='hidden' if args.operation in ('hide','unhide') else 'archived'
        record[field]=args.operation in ('hide','archive')
    elif args.operation=='bind':
        if not args.conversation:raise ValueError('bind requires --conversation with the actual provider conversation ID')
        if record['agent'] in ('claude','codex'):
            value=str(uuid.UUID(args.conversation))
        else:
            import re
            value=args.conversation
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}',value):
                raise ValueError('Invalid provider conversation ID')
        if record.get('conversation_id') and record['conversation_id']!=value:raise ValueError('Conversation ID is already bound; refusing reassignment')
        record['conversation_id']=value
    elif args.operation=='forget':
        if not args.yes:raise ValueError('forget requires --yes; removes only utility metadata, preserving provider history')
        if ag.dry:print('Would forget '+key);return
        with locked_registry() as registry:registry['sessions'].pop(key,None)
        return
    elif args.operation in ('status','finish'):
        if args.operation=='finish':
            if not args.result:raise ValueError('finish requires --result with a task outcome or result-file path')
            record['result']=args.result;record['status']='failed' if args.failed else 'completed'
        else:
            if not args.state:raise ValueError('status requires --state')
            record['status']=args.state
    else:raise ValueError('Unsupported session action')
    record['updated_at']=now()
    if ag.dry:print(json.dumps(record,indent=2));return
    save(record)
    if args.operation=='bind':
        from .restore import pin_one
        pin_one(ag,key)
    if args.operation in ('status','finish'):
        # Result is durable even when the terminal disappeared before notification.
        live=next((r for r in live_rows(ag) if r['id']==record['terminal_id']),None)
        if not live:print('Saved result/status; terminal is no longer open');return
        target=['--target',live['id'],'--window',live['window_id']]
        state={'running':'active','failed':'blocked'}.get(record['status'],record['status'])
        ag.call('session','status',state,*target)
        if args.operation=='finish':
            ag.call('notify',args.result[:400],'--title',record['title'],*target)
            with locked_registry() as registry:coordinator=registry['coordinators'].get(record['project'])
            if coordinator and coordinator.get('terminal_id')!=record['terminal_id']:
                parent=next((r for r in live_rows(ag) if r['id']==coordinator.get('terminal_id')),None)
                if parent:ag.call('notify',record['title']+': '+args.result[:300],'--target',parent['id'],'--window',parent['window_id'])


def task_project(project,args,dry):
    """Task launches get their own roots unless explicitly run in-place."""
    from ..cli import run
    if not args.task:raise ValueError('task requires --task ID')
    subprocess.run(['git','check-ref-format','--branch',args.task],check=True,stdout=subprocess.DEVNULL)
    if args.in_place:return project
    repos=args.repo or project['roots']
    # Auto worktrees support repository roots only; other roots require choosing
    # explicit repositories or the explicit --in-place escape hatch.
    sources=[Path(r).expanduser().resolve() for r in repos]
    dest=Path(args.destination).expanduser().resolve() if args.destination else state_dir()/'worktrees'/uuid.uuid5(uuid.NAMESPACE_URL,project['id']).hex[:12]/args.task
    targets=[dest/r.name for r in sources]
    if len(set(targets))!=len(targets):raise ValueError('Repository basenames collide')
    for repo,target in zip(sources,targets):
        actual=Path(run(['git','-C',str(repo),'rev-parse','--show-toplevel'])).resolve()
        if repo!=actual:raise ValueError('Task roots must be repository roots: '+str(repo))
        if target.exists():raise ValueError('Worktree target already exists: '+str(target))
        run(['git','-C',str(repo),'rev-parse','--verify',args.base])
        if run(['git','-C',str(repo),'status','--porcelain']):raise ValueError('Dirty repository: '+str(repo))
        branch=subprocess.run(['git','-C',str(repo),'show-ref','--verify','--quiet','refs/heads/'+args.task])
        if branch.returncode==0:raise ValueError('Task branch already exists: '+args.task)
        if branch.returncode!=1:raise ValueError('Cannot inspect task branch')
    for repo,target in zip(sources,targets):
        argv=['git','-C',str(repo),'worktree','add','-b',args.task,str(target),args.base]
        if dry:print(shlex.join(argv))
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run(argv,check=True)
    return {**project,'roots':[str(t) for t in targets]}


def launch_in_place(window,project,agent,resume,conversation=None):
    """Register a startup-menu launch without creating a second terminal row."""
    from argparse import Namespace
    from ..cli import Agterm
    class CurrentTerminal(Agterm):
        def launch(self,window,project,argv,cwd):
            self.argv=argv;self.cwd=cwd
            return os.environ['AGTERM_SESSION_ID']
    ag=CurrentTerminal()
    launch(ag,window,project,agent,Namespace(session=conversation,prompt=None,
           command='resume' if resume else 'new',task=None))
    if not hasattr(ag,'argv'):
        from .startup import plain_shell
        return plain_shell()
    os.chdir(ag.cwd)
    os.execv(shutil.which('env') or '/usr/bin/env',['env','AGTERM_UTILS_PLAIN_SHELL=1',*ag.argv[1:]])
