"""Explicit, locked edits to the external configuration."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import shutil
import shlex


@contextmanager
def edit_config(path, dry=False):
    from ..cli import atomic_write, load
    path=Path(path).expanduser()
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix(path.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        data=load(path) if path.exists() else {'version':1,'windows':[]}
        yield data
        # Validate before touching the existing file, including in preview mode.
        from ..cli import validate
        validate(data)
        if dry:
            print(json.dumps(data,indent=2));return
        if path.exists():
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            shutil.copy2(path,path.with_name(path.name+'.backup-'+stamp))
        atomic_write(path,data)


def configure(args):
    with edit_config(args.config,args.dry_run) as data:
        windows=data['windows']
        if args.operation=='add':
            if not args.project or not args.window or not args.root:
                raise ValueError('project add requires --project ID --window NAME --root PATH (repeatable)')
            if any(p['id']==args.project for w in windows for p in w.get('projects',[])):
                raise ValueError('Project ID already exists')
            window=next((w for w in windows if w['name']==args.window),None)
            if window is None:
                if not args.agent:raise ValueError('New window requires --agent with a supported provider as its default')
                window={'name':args.window,'default_agent':args.agent,'projects':[]};windows.append(window)
            project={'id':args.project,'name':args.name or args.project,
                     'roots':[str(Path(r).expanduser().resolve()) for r in args.root]}
            if args.agent:project['default_agent']=args.agent
            window.setdefault('projects',[]).append(project)
        else:
            matches=[(w,p) for w in windows for p in w.get('projects',[]) if p['id']==args.project]
            if len(matches)!=1:raise ValueError('Select an existing --project ID')
            window,project=matches[0]
            if args.operation=='edit':
                if args.name:project['name']=args.name
                if args.root:project['roots']=[str(Path(r).expanduser().resolve()) for r in args.root]
                if args.agent:project['default_agent']=args.agent
            elif args.operation in ('enable','disable'):project['enabled']=args.operation=='enable'
            elif args.operation=='move':
                if not args.window:raise ValueError('project move requires --window')
                target=next((w for w in windows if w['name']==args.window),None)
                if target is None:raise ValueError('Destination window does not exist')
                window['projects'].remove(project);target.setdefault('projects',[]).append(project)
                window=target
            elif args.operation!='order':raise ValueError('Unknown project operation')
            elif args.position is None:raise ValueError('project order requires --position (1-based)')
        if args.position is not None:
            items=window['projects']
            if not 1<=args.position<=len(items):raise ValueError('--position must be within the window project list (1-based)')
            items.remove(project);items.insert(args.position-1,project)
    if args.send:
        from ..cli import main
        command=['--config',args.config]
        if args.dry_run:command+=['--dry-run']
        main(command+['bootstrap','--project',args.project])


def provider_settings_menu(path):
    from ..cli import pick,load,AGENTS,AGENT_NAMES
    while True:
        settings=load(path).get('providers',{})
        automatic=settings.get('auto_detect',True)
        key='hidden' if automatic else 'enabled'
        values=settings.get(key,[])
        choices=[('Auto-detect: '+('On' if automatic else 'Off'),'auto')]
        for agent in AGENTS:
            state=('hidden' if agent in values else 'shown when installed') if automatic else ('enabled' if agent in values else 'disabled')
            choices.append((AGENT_NAMES[agent]+' · '+state,agent))
        choices.extend([('Launch arguments','launch-args'),('Done',None)])
        choice=pick('Providers · toggle a setting',choices)
        if choice is None:return
        if choice=='launch-args':
            provider_launch_args_menu(path);continue
        with edit_config(path) as data:
            settings=data.setdefault('providers',{})
            if choice=='auto':settings['auto_detect']=not settings.get('auto_detect',True)
            else:
                key='hidden' if settings.get('auto_detect',True) else 'enabled'
                values=settings.setdefault(key,[])
                if choice in values:values.remove(choice)
                else:values.append(choice)


def settings_command(args):
    from ..cli import load
    zellij_changes={k:v for k,v in (('layout',getattr(args,'zellij_layout',None)),('session',getattr(args,'zellij_session',None))) if v is not None}
    changing=args.auto_detect is not None or args.hide_agent or args.show_agent or args.enabled_agent is not None or args.launch_args is not None or args.agent_order is not None or bool(zellij_changes)
    if args.operation=='list':
        if changing:raise ValueError('settings list does not accept changes')
        from .startup import visible_agents
        data=load(args.config)
        print(json.dumps({'providers':{'auto_detect':True,'hidden':[],'enabled':[],**data.get('providers',{})},'visible':visible_agents(data)},indent=2));return
    if args.operation not in (None,'edit'):raise ValueError('Use settings, settings edit, or settings list')
    if not changing:
        if args.dry_run:print('Would open provider settings');return
        return provider_settings_menu(args.config)
    if zellij_changes:
        if bool(args.window)==bool(args.project):raise ValueError('--zellij-layout/--zellij-session require exactly one of --window or --project')
        with edit_config(args.config,args.dry_run) as data:
            set_zellij(data,args.window,args.project,zellij_changes)
        if args.agent_order is None:return
    if args.agent_order is not None:
        if bool(args.window)==bool(args.project):raise ValueError('--agent-order requires exactly one of --window or --project')
        order=[x.strip() for x in args.agent_order.split(',') if x.strip()]
        from ..cli import check_agent_order
        check_agent_order(order)
        with edit_config(args.config,args.dry_run) as data:
            if args.window:
                target=next((w for w in data['windows'] if w['name']==args.window),None)
            else:
                target=next((p for w in data['windows'] for p in w.get('projects',[]) if p['id']==args.project),None)
            if target is None:raise ValueError('Window or project not found')
            if order:target['agent_order']=order
            else:target.pop('agent_order',None)
        return
    if args.launch_args is not None and not args.agent:raise ValueError('--launch-args requires --agent')
    parsed=shlex.split(args.launch_args) if args.launch_args is not None else None
    with edit_config(args.config,args.dry_run) as data:
        settings=data.setdefault('providers',{})
        if parsed is not None:settings.setdefault('launch_args',{})[args.agent]=parsed
        if args.auto_detect is not None:settings['auto_detect']=args.auto_detect=='on'
        if args.enabled_agent is not None:settings['enabled']=list(dict.fromkeys(args.enabled_agent))
        hidden=settings.setdefault('hidden',[])
        for agent in args.hide_agent:
            if agent not in hidden:hidden.append(agent)
        for agent in args.show_agent:
            if agent in hidden:hidden.remove(agent)


def set_zellij(data,window,project,changes):
    """Set or clear (empty value) zellij.layout / zellij.session on one window or project."""
    if window:target=next((w for w in data['windows'] if w['name']==window),None)
    else:target=next((p for w in data['windows'] for p in w.get('projects',[]) if p['id']==project),None)
    if target is None:raise ValueError('Window or project not found')
    zellij=dict(target.get('zellij') or {})
    for key,value in changes.items():
        if value in ('',None):zellij.pop(key,None)
        else:zellij[key]=value
    if zellij:target['zellij']=zellij
    else:target.pop('zellij',None)


def provider_launch_args_menu(path):
    from ..cli import pick,load,AGENTS,AGENT_NAMES
    while True:
        current=load(path).get('providers',{}).get('launch_args',{})
        entries=[(AGENT_NAMES[agent]+' · '+(shlex.join(current.get(agent,[])) or 'default'),agent) for agent in AGENTS]
        agent=pick('Launch arguments',entries+[('Done',None)])
        if agent is None:return
        print('Current arguments: '+(shlex.join(current.get(agent,[])) or '(none)'))
        value=input('Arguments only (Enter keeps current; - clears): ').strip()
        if not value:continue
        values=[] if value=='-' else shlex.split(value)
        with edit_config(path) as data:
            data.setdefault('providers',{}).setdefault('launch_args',{})[agent]=values
