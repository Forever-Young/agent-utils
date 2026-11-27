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


def change_roots(project,replace=(),add=(),remove=()):
    """Edit project['roots'] in place: replace them all, or add and remove single roots."""
    norm=lambda r:str(Path(r).expanduser().resolve())
    if replace and (add or remove):raise ValueError('--root replaces the whole list; do not combine it with --add-root or --remove-root')
    if replace:project['roots']=[norm(r) for r in replace];return
    roots=list(project.get('roots',[]))
    for r in remove:
        if norm(r) not in roots:raise ValueError('Not a root of this project: '+norm(r))
        roots.remove(norm(r))
    for r in add:
        if norm(r) in roots:raise ValueError('Already a root of this project: '+norm(r))
        roots.append(norm(r))
    if not roots:raise ValueError('A project needs at least one root')
    project['roots']=roots


def slot_order(current,wanted):
    """`current` with the entries also in `wanted` refilled in `wanted` order; others keep their slots."""
    present=[n for n in wanted if n in current]
    it=iter(present)
    return [next(it) if n in present else n for n in current]


def _tree(ag,window):
    """(window id, named workspaces in sidebar order) of an existing window, else (None, [])."""
    from ..cli import Agterm
    items=Agterm().call('window','list',json_result=True)
    if isinstance(items,dict):items=items.get('windows',[])
    matches=[w for w in items if w.get('name')==window and not w.get('closed')]
    if len(matches)>1:raise ValueError('Ambiguous window name: '+window)
    if not matches:return None,[]
    wid=matches[0]['id']
    tree=Agterm().call('tree','--window',wid,json_result=True) or {}
    tree=tree.get('tree',tree)
    return wid,[(w['name'],w['id']) for w in tree.get('workspaces',[]) if w.get('name')]


def push_order(path,dry=False,window=None):
    """Reorder agterm workspaces to the configured project order; other workspaces keep their slots."""
    from ..cli import Agterm,load
    ag=Agterm(dry)
    for w in load(Path(path).expanduser())['windows']:
        if window and w['name']!=window:continue
        wid,rows=_tree(ag,w['name'])
        if wid is None:print('Window %s is not open in agterm; skipped'%w['name']);continue
        names=[n for n,_ in rows];ids=dict(rows)
        target=slot_order(names,[p['name'] for p in w.get('projects',[])])
        sim=list(names)
        for i,name in enumerate(target):
            while sim.index(name)>i:
                ag.call('workspace','move','--to','up','--target',ids[name],'--window',wid)
                j=sim.index(name);sim[j-1],sim[j]=sim[j],sim[j-1]
        if sim==names:print('%s: already in order'%w['name']);continue
        if not dry:
            after=[n for n,_ in _tree(ag,w['name'])[1]]
            if after!=target:raise ValueError('%s: agterm order is now %s, expected %s'%(w['name'],after,target))
        print('%s: %s'%(w['name'],', '.join(target)))


def pull_order(path,dry=False,window=None):
    """Reorder each window's configured projects to match the agterm sidebar order."""
    from ..cli import Agterm
    with edit_config(path,dry) as data:
        for w in data['windows']:
            if window and w['name']!=window:continue
            pull_window_order(w)


def pull_window_order(w):
    wid,rows=_tree(None,w['name'])
    if wid is None:print('Window %s is not open in agterm; skipped'%w['name']);return
    projects=w.get('projects',[]);by_name={p['name']:p for p in projects}
    order=slot_order([p['name'] for p in projects],[n for n,_ in rows])
    w['projects']=[by_name[n] for n in order]


def configure(args):
    if args.operation in ('push-order','pull-order'):
        return (push_order if args.operation=='push-order' else pull_order)(args.config,args.dry_run,args.window)
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
                change_roots(project,args.root,args.add_root,args.remove_root)
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
        if args.operation=='order':push_order(args.config,args.dry_run,window['name'])


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
