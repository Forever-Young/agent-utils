"""Keep agterm's restart pins current so reopened sessions resume their agents.

agterm restores windows, workspaces, session names, cwd and layout by itself, and
replays a captured foreground command. For a managed agent that command is a bare
provider launch, which would start a new conversation or reopen the startup menu.
This module pins, per pane, the provider-native resume command for the exact
conversation in the registry (`agtermctl session restore`). It never types into a
pane and never resumes anything itself. Pins hold shell code, so they carry only
registry-validated values and no secrets.
"""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shlex
import re
import subprocess
import uuid

from ..core.files import atomic_write
from .state import state_dir

SNAPSHOT='restore-snapshot.json'
KEEP_BACKUPS=3
MARKER='AGTERM_UTILS_RECORD='


def resume_argv(record,config_path=None):
    """Provider argv that resumes this record's exact conversation, or None."""
    if not record.get('conversation_id') or not record.get('roots'):return None
    from ..cli import agent_argv,load,with_provider_launch_args
    project={'id':record['project'],'name':record['project_name'],'roots':record['roots'],
             'session_title':record['title'],'agent_args':record.get('agent_args',{})}
    if config_path and Path(config_path).exists():
        try:project=with_provider_launch_args(project,load(config_path))
        except (OSError,ValueError):pass
    try:argv,_=agent_argv(project,record['agent'],record['conversation_id'])
    except ValueError:return None
    if record['agent']=='codex':argv.append('--no-daemon')
    return argv


def pin_line(record,config_path=None):
    """Shell line that resumes this record's exact conversation, or None."""
    if not record.get('terminal_id') or record.get('hidden') or record.get('archived'):return None
    argv=resume_argv(record,config_path)
    if argv is None:return None
    from .runner import wrapped
    cwd=record.get('cwd') or record['roots'][0]
    return 'cd '+shlex.quote(str(cwd))+' && '+shlex.join(['env','AGTERM_UTILS_PLAIN_SHELL=1',*wrapped(record['id'],argv)])


def owned(pin,record_id):
    return bool(pin) and MARKER+record_id in pin


def _rows(ag):
    """Live rows of every open window. A window that is not open has no tree; skip it."""
    windows=ag.call('window','list',json_result=True)
    if isinstance(windows,dict):windows=windows.get('windows',[])
    rows={}
    for window in windows or []:
        if window.get('closed'):continue
        try:tree=ag.call('tree','--window',window['id'],json_result=True)
        except (OSError,subprocess.SubprocessError,ValueError):continue
        tree=tree.get('tree',tree)
        for workspace in tree.get('workspaces',[]):
            for row in workspace.get('sessions',[]):
                rows[row['id']]={**row,'window_id':window['id'],'window_name':window['name'],'workspace_name':workspace['name']}
    return rows


def _process_table():
    out=subprocess.run(['ps','-axo','pid=,ppid='],capture_output=True,text=True,timeout=10).stdout
    table={}
    for line in out.splitlines():
        parts=line.split()
        if len(parts)==2 and all(x.isdigit() for x in parts):table[int(parts[0])]=int(parts[1])
    return table


def _descends(table,pid,root):
    for _ in range(16):
        if pid==root:return True
        pid=table.get(pid)
        if not pid:return False
    return False


def _alive(record):
    from .workers import process_birth
    pid=record.get('provider_pid');birth=record.get('provider_birth')
    return bool(pid and birth and process_birth(pid)==birth)


def discover(record,home=None,table=None):
    """Conversation id of a running managed provider, or None unless unambiguous.

    Codex keeps its rollout file open; Kiro keeps a lease file naming its pid and
    parent pid. Both are read-only observations of the running process.
    """
    home=Path(home or Path.home());agent=record.get('agent')
    if not _alive(record):return None
    root=int(record['provider_pid']);found=set()
    if agent=='codex':
        pids=[root]+[p for p in (table or _process_table()) if _descends(table or _process_table(),p,root)]
        out=subprocess.run(['lsof','-Fn','-p',','.join(str(p) for p in dict.fromkeys(pids))],capture_output=True,text=True,timeout=20).stdout
        for line in out.splitlines():
            match=re.search(r'/\.codex/sessions/.*rollout-[0-9T-]+-([0-9a-f-]{36})\.jsonl$',line)
            if match:
                try:found.add(str(uuid.UUID(match.group(1))))
                except ValueError:pass
    elif agent=='kiro-cli':
        table=table or _process_table()
        for lease in (home/'.kiro/session-leases').glob('sess_*.lease'):
            try:data=json.loads(lease.read_text())
            except (OSError,ValueError):continue
            if isinstance(data.get('pid'),int) and _descends(table,data['pid'],root) and re.fullmatch(r'sess_[0-9a-f-]{36}',lease.stem):
                found.add(lease.stem)
    return found.pop() if len(found)==1 else None


PROVIDER_BINARIES={'kiro-cli':'kiro-cli','codex':'codex'}


def session_processes(session_ids):
    """Provider processes that carry an agterm session id in their environment."""
    out=subprocess.run(['ps','axeww','-o','pid=,ppid=,command='],capture_output=True,text=True,timeout=20).stdout
    found={}
    for line in out.splitlines():
        parts=line.split(None,2)
        if len(parts)<3 or not parts[0].isdigit():continue
        match=re.search(r'\bAGTERM_SESSION_ID=([0-9A-Fa-f-]{36})\b',parts[2])
        if not match or match.group(1) not in session_ids:continue
        executable=os.path.basename(parts[2].split(None,1)[0])
        if executable in PROVIDER_BINARIES.values() and '--' not in parts[2].split(None,1)[0]:
            found.setdefault(match.group(1),[]).append((int(parts[0]),int(parts[1]),executable))
    return found


def adopt_unregistered(rows,home=None):
    """Register open panes running Kiro or Codex that no record covers (started by hand)."""
    from .sessions import locked_registry,now
    from .workers import process_birth
    known={r.get('terminal_id') for r in __import__('agent_utils.agterm.sessions',fromlist=['records']).records().values()}
    wanted={rid for rid,row in rows.items() if rid not in known and not row.get('restoreCommand')}
    if not wanted:return []
    table=_process_table();adopted=[]
    for session,items in session_processes(wanted).items():
        pids={pid for pid,_,_ in items}
        tops=[(pid,name) for pid,ppid,name in items if ppid not in pids]
        if len(tops)!=1:continue
        pid,name=tops[0];agent=next(a for a,b in PROVIDER_BINARIES.items() if b==name)
        probe={'agent':agent,'provider_pid':pid,'provider_birth':process_birth(pid)}
        conversation=discover(probe,home,table)
        if not conversation:continue
        row=rows[session];cwd=row.get('cwd') or str(Path.home())
        with locked_registry() as registry:
            sessions=registry.setdefault('sessions',{})
            if any(r.get('conversation_id')==conversation and _alive(r) for r in sessions.values()):continue
            key=str(uuid.uuid4())
            sessions[key]={'id':key,'project':'manual','project_name':row['workspace_name'],'roots':[cwd],
                'agent':agent,'title':clean_name(row.get('name')) or row['workspace_name'],'agent_args':{},
                'conversation_id':conversation,'created_at':now(),'updated_at':now(),'hidden':False,'archived':False,
                'status':'adopted','cwd':cwd,'terminal_id':session,'window':row['window_name'],
                'provider_pid':pid,'provider_birth':probe['provider_birth'],
                'aliases':[],'prs':[],'branches':[],'repos':[]}
        adopted.append(key)
    return adopted


def discover_by_session(record,home=None,table=None):
    """For a record with no tracked provider process: find the pane's own provider process."""
    from .workers import process_birth
    items=session_processes({record.get('terminal_id')}).get(record.get('terminal_id'),[])
    pids={pid for pid,_,_ in items}
    tops=[pid for pid,ppid,name in items if ppid not in pids and name==PROVIDER_BINARIES.get(record.get('agent'))]
    if len(tops)!=1:return None
    probe={'agent':record['agent'],'provider_pid':tops[0],'provider_birth':process_birth(tops[0])}
    found=discover(probe,home,table)
    if found:record.update(provider_pid=probe['provider_pid'],provider_birth=probe['provider_birth'])
    return found


def bind_discovered(rows,home=None):
    """Record conversation ids of live managed rows that have none. Never reassigns."""
    from .sessions import locked_registry,now
    table=_process_table();bound=[]
    with locked_registry() as registry:
        sessions=registry.get('sessions',{})
        # An id held by a record whose provider is gone (the conversation was reopened in a new
        # pane) may be bound again; an id held by a live provider never is.
        taken={r.get('conversation_id') for r in sessions.values() if r.get('conversation_id') and _alive(r)}
        for key,record in sessions.items():
            if record.get('conversation_id') or record.get('terminal_id') not in rows:continue
            if record.get('agent') not in ('codex','kiro-cli'):continue
            found=discover(record,home,table) if _alive(record) else discover_by_session(record,home,table)
            if found and found not in taken:
                record.update(conversation_id=found,updated_at=now());taken.add(found);bound.append(key)
    return bound


ENDED_GRACE=60


def ended(record,row,live_panes,clock=None):
    """True once an agent has stayed ended in a pane that is now something else.

    Seen twice at least ENDED_GRACE seconds apart, so a pane replaying its restart
    command right after a launch (shell first, provider a moment later) is not cleared.
    """
    from .sessions import locked_registry
    clock=clock or datetime.now(timezone.utc).timestamp
    foreground=' '.join(row.get('foreground') or [])
    running=row['id'] in live_panes or 'runner.py' in foreground or os.path.basename(
        (row.get('foreground') or [''])[0]) in (record.get('agent'),'kiro-cli','codex','claude')
    gone=row.get('realized',True) and not running and not _alive(record)
    with locked_registry() as registry:
        stored=registry.get('sessions',{}).get(record['id'])
        if stored is None:return False
        if not gone:
            stored.pop('ended_seen_at',None);return False
        first=stored.setdefault('ended_seen_at',clock())
    return clock()-first>=ENDED_GRACE


def sync(ag,config_path=None,prune=True):
    """Pin each live managed row to its resume command. Returns a list of actions."""
    from .sessions import records
    rows=_rows(ag);actions=[]
    actions+=[('bound',key) for key in bind_discovered(rows)]
    actions+=[('adopted',key) for key in adopt_unregistered(rows)]
    items=list(records().values())
    live_panes={r.get('terminal_id') for r in items if _alive(r)}
    for record in items:
        row=rows.get(record.get('terminal_id'))
        if row is None:continue
        want=pin_line(record,config_path);have=row.get('restoreCommand')
        if want and not have and row['id'] not in live_panes:continue  # agent gone: never re-pin
        if want and (not have or owned(have,record['id'])) and ended(record,row,live_panes):
            if have:
                ag.call('session','restore','--clear','--target',row['id'],'--window',row['window_id'])
                actions.append(('cleared-ended',record['id']))
            continue
        target=['--target',row['id'],'--window',row['window_id']]
        if have and not owned(have,record['id']):
            actions.append(('kept-foreign-pin',record['id']));continue
        if want and want!=have:
            ag.call('session','restore',want,*target);actions.append(('pinned',record['id']))
        elif not want and have and prune:
            ag.call('session','restore','--clear',*target);actions.append(('cleared',record['id']))
        elif not want and record.get('conversation_id') is None and record.get('agent') and not record.get('archived'):
            actions.append(('needs-bind',record['id']))
    return actions


def pin_one(ag,record_id,config_path=None):
    """Best-effort pin right after a launch; failures never break the launch."""
    try:
        if getattr(ag,'dry',False):return
        from .sessions import records
        record=records().get(record_id)
        want=pin_line(record,config_path) if record else None
        if want:
            ag.call('session','restore',want,'--target',record['terminal_id'])
    except (OSError,ValueError,subprocess.SubprocessError):pass


def snapshot_data(ag):
    from .sessions import records
    by_terminal={r.get('terminal_id'):r for r in records().values() if r.get('terminal_id')}
    sessions=[]
    for row in _rows(ag).values():
        record=by_terminal.get(row['id']) or {}
        sessions.append({'terminal_id':row['id'],'window':row['window_name'],'workspace':row['workspace_name'],
                         'name':row.get('name'),'cwd':row.get('cwd'),'record':record.get('id'),
                         'agent':record.get('agent'),'conversation_id':record.get('conversation_id'),
                         'pinned':bool(row.get('restoreCommand'))})
    return {'version':1,'at':datetime.now(timezone.utc).isoformat(),'sessions':sessions}


def write_snapshot(ag):
    path=state_dir()/SNAPSHOT
    data=snapshot_data(ag)
    if path.exists():
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path.rename(path.with_name(SNAPSHOT+'.'+stamp+'.bak'))
        for old in sorted(path.parent.glob(SNAPSHOT+'.*.bak'))[:-KEEP_BACKUPS]:old.unlink()
    atomic_write(path,data)
    return path,data


def plan(ag):
    """Differences between the last snapshot and the live tree. Read-only."""
    path=state_dir()/SNAPSHOT
    if not path.exists():raise ValueError('No snapshot yet; run: agents agterm restore snapshot')
    saved=json.loads(path.read_text())
    if saved.get('version')!=1:raise ValueError('Unsupported snapshot version')
    live={s['terminal_id']:s for s in snapshot_data(ag)['sessions']}
    report=[]
    for old in saved['sessions']:
        now=live.get(old['terminal_id'])
        if now is None:report.append(('missing',old));continue
        if old.get('name')!=now.get('name'):report.append(('renamed',{**old,'now':now.get('name')}))
        if old.get('agent') and not now['pinned']:report.append(('unpinned',old))
    for terminal,now in live.items():
        if terminal not in {s['terminal_id'] for s in saved['sessions']}:report.append(('new',now))
    return report


def clean_name(name):
    return re.sub(r'^[^\w]+','',name or '').strip()


def kiro_candidates(cwd,home=None,limit=15):
    """Recent Kiro conversations whose workspace is this directory, newest first."""
    found=[]
    for meta in Path(home or Path.home()).glob('.kiro/sessions/*/sess_*/session.json'):
        try:data=json.loads(meta.read_text())
        except (OSError,ValueError):continue
        if str(cwd) in (data.get('workspacePaths') or [])+(data.get('rootPaths') or []):
            found.append((data.get('lastModifiedAt',''),data.get('id') or meta.parent.name,data.get('title') or ''))
    return sorted(found,reverse=True)[:limit]


def valid_conversation(agent,value):
    if agent in ('claude','codex'):return str(uuid.UUID(value))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}',value or ''):raise ValueError('Invalid provider conversation ID')
    return value


def pin_session(ag,args,config):
    """Choose an open session and agent, then give that pane a resume command."""
    from ..cli import AGENTS,AGENT_NAMES,pick
    from .sessions import records,save,now
    from ..cli import Agterm
    rows=_rows(Agterm() if ag.dry else ag)
    if args.target:
        row=rows.get(args.target)
        if row is None:raise ValueError('Session not found among open windows: '+args.target)
    else:
        row=pick('Session',[(r['window_name']+' / '+r['workspace_name']+' / '+clean_name(r.get('name')),r) for r in rows.values()])
        if row is None:return
    existing=next((r for r in records().values() if r.get('terminal_id')==row['id']),None)
    agent=args.agent or (existing or {}).get('agent') or pick('Agent',[(AGENT_NAMES[a],a) for a in AGENTS])
    if agent is None:return
    conversation=args.conversation or (existing or {}).get('conversation_id')
    cwd=row.get('cwd') or str(Path.cwd())
    if not conversation and agent=='kiro-cli':
        choice=pick('Kiro conversation in '+cwd,[(when[:19]+'  '+title[:60]+'  '+sid,sid) for when,sid,title in kiro_candidates(cwd)])
        if choice is None:return
        conversation=choice
    if not conversation:raise ValueError('Pass --conversation with the provider conversation ID')
    conversation=valid_conversation(agent,conversation)
    record=dict(existing) if existing else {
        'id':str(uuid.uuid4()),'project':'manual','project_name':row['workspace_name'],'roots':[cwd],
        'agent':agent,'title':clean_name(row.get('name')) or row['workspace_name'],'agent_args':{},
        'created_at':now(),'hidden':False,'archived':False,'status':'adopted','cwd':cwd,
        'terminal_id':row['id'],'window':row['window_name'],'aliases':[],'prs':[],'branches':[],'repos':[]}
    record.update(agent=agent,conversation_id=conversation,updated_at=now())
    if ag.dry:
        print(pin_line(record,config));return
    save(record)
    ag.call('session','restore',pin_line(record,config),'--target',row['id'],'--window',row['window_id'])
    print('Pinned '+clean_name(row.get('name'))+' to '+agent+' conversation '+conversation)


def note_run(count,source):
    """Append one line per sync run so hook activity is visible; keep the last 200."""
    path=state_dir()/'restore-sync.log'
    try:
        lines=path.read_text().splitlines()[-199:] if path.exists() else []
        lines.append(datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')+' '+source+' actions='+str(count))
        path.write_text('\n'.join(lines)+'\n')
    except OSError:pass


def command(args,ag):
    from ..cli import config_path
    config=getattr(args,'config',None) or str(config_path())
    operation=args.operation
    if operation=='sync':
        if ag.dry:print('Would pin resume commands for live managed sessions');return
        actions=sync(ag,config)
        for what,record in actions:print(what+' '+record)
        note_run(len(actions),os.environ.get('AGT_EVENT_KIND') or 'manual')
    elif operation=='snapshot':
        if ag.dry:print('Would write '+str(state_dir()/SNAPSHOT));return
        path,data=write_snapshot(ag);print('Wrote '+str(path)+' ('+str(len(data['sessions']))+' sessions)')
    elif operation=='pin':pin_session(ag,args,config)
    elif operation=='plan':
        report=plan(ag)
        for what,item in report:print(what+' '+item.get('name','?')+' ['+item['terminal_id'][:8]+']'+(' now: '+str(item.get('now')) if what=='renamed' else ''))
        if not report:print('No differences from the last snapshot')
    else:raise ValueError('restore requires sync, snapshot, plan or pin')
