"""Default terminal command: resolve workspace, select action, run in-place."""
import os
from pathlib import Path
import shutil
import subprocess


def resolve(data, tree, workspace_id=None, session_id=None, cwd=None):
    from ..cli import projects
    tree=tree.get('tree',tree)
    workspaces=tree.get('workspaces',[])
    # Session membership wins over inherited workspace environment after a move.
    owning=next((w for w in workspaces if any(s.get('id')==session_id for s in w.get('sessions',[]))),None)
    if owning is None and workspace_id:
        owning=next((w for w in workspaces if w.get('id')==workspace_id),None)
    if owning:
        matches=[(w,p) for w,p in projects(data) if p['name']==owning.get('name')]
        # A repeated workspace name across windows requires an explicit window map.
        window_name=tree.get('windowName') or tree.get('name')
        if window_name:
            matches=[(w,p) for w,p in matches if w['name']==window_name]
        return matches[0] if len(matches)==1 else None
    if workspace_id or session_id:
        return None  # Do not select by a stale or misleading inherited cwd.
    if cwd:
        path=Path(cwd).resolve()
        matches=[]
        for w,p in projects(data):
            for root in p['roots']:
                root=Path(root).expanduser().resolve()
                if root==path or root in path.parents:matches.append((len(root.parts),w,p))
        if matches:
            depth=max(x[0] for x in matches)
            candidates={p['id']:(w,p) for n,w,p in matches if n==depth}
            if len(candidates)==1:return next(iter(candidates.values()))
    return None


def plain_shell():
    shell=os.environ.get('SHELL','/bin/zsh')
    if not Path(shell).is_absolute() or not Path(shell).is_file():shell='/bin/zsh'
    os.environ['AGTERM_UTILS_PLAIN_SHELL']='1'
    os.execv(shell,[shell,'-l'])


def visible_agents(data):
    from ..cli import AGENTS,agent_executable
    settings=data.get('providers',{})
    if settings.get('auto_detect',True):
        hidden=settings.get('hidden',[])
        return [agent for agent in AGENTS if agent not in hidden and agent_executable(agent)]
    return list(settings.get('enabled',[]))


def ordered_agents(available,default,order=None):
    """Apply an agent_order list: provider names in order, '*' = all the rest.

    Without '*', unlisted providers are left out of the menu. No order: default first."""
    if not order:
        return ([default] if default in available else [])+[a for a in available if a!=default]
    listed=[a for a in order if a!='*']
    rest=[a for a in available if a not in listed]
    result=[]
    for item in order:
        if item=='*':result+=rest;rest=[]
        elif item in available:result.append(item)
    return result


def startup_choices(default,data=None,order=None):
    from ..cli import AGENTS,AGENT_NAMES
    available=visible_agents(data) if data is not None else list(AGENTS)
    agents=ordered_agents(available,default,order)
    choices=[(AGENT_NAMES[agent],agent) for agent in agents]
    return choices+[('Plain shell',None)]


def pick_provider(title,default,data,config,dry=False,order=None,back=False):
    from ..cli import pick
    return pick(title,startup_choices(default,data,order),back=back)


def choose_session(project,agent,back=False,coordinator=True):
    """New first, then project-scoped provider sessions; None means cancel."""
    from ..cli import pick,AGENT_NAMES
    from .history import claude_history,codex_history,opencode_session_entries
    entries=[('New session',('new',None))]
    if coordinator and agent in COORDINATOR_AGENTS:entries.append(('Coordinator mode',('coordinator',None)))
    sessions=[]
    try:
        if agent=='opencode':
            sessions=opencode_session_entries(project)
        elif agent in ('claude','codex'):
            roots=[str(Path(r).expanduser().resolve()) for r in project['roots']]
            rows=(claude_history if agent=='claude' else codex_history)(roots,100)
            sessions=[(row['title']+(' · '+row['conversation_id'] if row['title'].startswith(('Claude ','Codex ')) else ''),row['conversation_id']) for row in rows]
        else:sessions=[]
        entries += [(label,('resume',ident)) for label,ident in sessions]
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        print('Session list unavailable: '+str(exc))
    from ..cli import SEPARATOR
    if sessions:entries.insert(len(entries)-len(sessions),SEPARATOR);entries.append(SEPARATOR)
    if agent=='agy':entries.append(('Continue most recent session',('resume',None)))
    elif agent!='opencode':entries.append(('Resume using provider picker',('resume',None)))
    return pick(project['name']+' / '+AGENT_NAMES[agent],entries,back=back)


def _cname(data):
    return {'name':data['coordinator_name']} if data.get('coordinator_name') else {}


def open_coordinator(window, project, position="default", name=None, agent=None):
    project = dict(project)
    if name: project.setdefault('coordinator_name', name)
    from argparse import Namespace
    from ..cli import Agterm, launch_coordinator
    return launch_coordinator(Agterm(session_position=position),window,project,
                             Namespace(agent=agent,session=None,prompt=None,dry_run=False))


SINGLE_ROOT_AGENTS=('kiro-cli','opencode','pi')
COORDINATOR_AGENTS=('claude','codex','kiro-cli')


def choose_single_root(project,agent,pick,back=False):
    """Agents that accept one root: let the user choose which root to use now."""
    from ..cli import AGENT_NAMES,BACK
    roots=project['roots']
    if agent not in SINGLE_ROOT_AGENTS or len(roots)<2:return project
    root=pick(AGENT_NAMES.get(agent,agent)+' supports one root. Choose root',[(str(r),r) for r in roots],**({'back':True} if back else {}))
    if root is None or root is BACK:return root
    return {**project,'roots':[root]}


def restart_pinned(tree,session_id):
    """A restored pane pinned by `restore sync` resumes by itself; skip the menu."""
    tree=tree.get('tree',tree) if isinstance(tree,dict) else {}
    for workspace in tree.get('workspaces',[]):
        for row in workspace.get('sessions',[]):
            if row.get('id')==session_id:return 'AGTERM_UTILS_RECORD=' in (row.get('restoreCommand') or '')
    return False


def startup(config):
    from ..cli import BACK,Agterm,load,pick,projects,agent_argv,agent_executable,with_provider_launch_args
    try:
        data=load(config)
        tree={}
        if os.environ.get('AGTERM_ENABLED')=='1':
            window=os.environ.get('AGTERM_WINDOW_ID')
            if window:
                ag=Agterm()
                tree=ag.call('tree','--window',window,json_result=True)
                inventory=ag.call('window','list',json_result=True)
                if isinstance(inventory,dict):inventory=inventory.get('windows',[])
                found=next((w for w in inventory if w.get('id')==window),None)
                if found:tree['windowName']=found['name']
        if os.environ.get('AGTERM_ENABLED')=='1' and os.environ.get('AGTERM_WINDOW_ID'):
            from .placement import place_once
            place_once(ag,os.environ['AGTERM_WINDOW_ID'],os.environ.get('AGTERM_SESSION_ID'),data.get('new_session_position','default'))
        if restart_pinned(tree,os.environ.get('AGTERM_SESSION_ID')):return plain_shell()
        selected=resolve(data,tree,os.environ.get('AGTERM_WORKSPACE_ID'),os.environ.get('AGTERM_SESSION_ID'),Path.cwd())
        auto_project=selected is not None
        step='project' if selected is None else 'provider'
        while True:
            if step=='project':
                selected=pick('Project',[('Plain shell',None)]+[(w['name']+' / '+p['name'],(w,p)) for w,p in projects(data)])
                if selected is None:return plain_shell()
                step='provider'
            window,project=selected
            if step=='provider':
                default=project.get('default_agent',window['default_agent'])
                order=project.get('agent_order',window.get('agent_order'))
                choice=pick_provider(project['name'],default,data,config,order=order,back=not auto_project)
                if choice is BACK:step='project';continue
                project=base=with_provider_launch_args(project,load(config))
                if choice is None:
                    os.chdir(Path(project['roots'][0]).expanduser())
                    return plain_shell()
                agent=choice
                step='root'
            if step=='root':
                rooted=choose_single_root(base,agent,pick,back=True)
                if rooted is BACK:step='provider';continue
                if rooted is None:return plain_shell()
                project=rooted
                step='session'
            action=choose_session(project,agent,back=True)
            if action is BACK:
                step='root' if agent in SINGLE_ROOT_AGENTS and len(base['roots'])>1 else 'provider'
                continue
            break
        if action is None:return plain_shell()
        operation,ident=action
        if operation=='coordinator':
            if data.get('new_session_position')=='top':open_coordinator(window,project,'top',agent=agent,**_cname(data))
            else:open_coordinator(window,project,agent=agent,**_cname(data))
            # The first-prompt hook owns the parent shell. Signal successful
            # handoff so only that disposable menu shell exits.
            return 10 if os.environ.get('AGTERM_UTILS_STARTUP_HANDOFF')=='1' else None
        resume=operation=='resume'
        if os.environ.get('AGTERM_ENABLED')=='1' and os.environ.get('AGTERM_SESSION_ID'):
            from .sessions import launch_in_place
            return launch_in_place(window,project,agent,resume,ident)
        command,cwd=agent_argv(project,agent,ident,resume_picker=resume)
        for root in project['roots']:
            if not Path(root).expanduser().is_dir():raise ValueError('Root unavailable: '+root)
        executable=agent_executable(agent)
        if not executable:raise ValueError('Executable unavailable: '+agent)
        os.chdir(cwd)
        # Agent runs in this terminal, not a second newly created row.
        os.execv(executable,command)
    except (OSError,ValueError,subprocess.SubprocessError,KeyboardInterrupt,EOFError) as exc:
        print('Startup menu: '+str(exc))
        return plain_shell()
