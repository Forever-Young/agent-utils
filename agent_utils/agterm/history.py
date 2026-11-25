"""Project-scoped metadata discovery. No transcript editing or conversion."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import time
import uuid


@contextmanager
def codex_server():
    env={k:v for k,v in os.environ.items() if not k.startswith(('AGTERM_','AGT_'))}
    process=subprocess.Popen([shutil.which('codex') or 'codex','app-server','--stdio'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
    selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ)
    buffer=b'';counter=0
    def request(method,params):
        nonlocal buffer,counter
        counter+=1;ident=counter
        process.stdin.write((json.dumps({'id':ident,'method':method,'params':params})+'\n').encode());process.stdin.flush()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            if b'\n' not in buffer:
                if not selector.select(max(0,deadline-time.monotonic())):break
                chunk=os.read(process.stdout.fileno(),65536)
                if not chunk:raise ValueError('Codex app-server exited before replying')
                buffer+=chunk
                if len(buffer)>16*1024*1024:raise ValueError('Codex response too large')
                continue
            line,buffer=buffer.split(b'\n',1)
            message=json.loads(line)
            if message.get('id')!=ident:continue
            if 'error' in message:raise ValueError('Codex metadata API: '+str(message['error'].get('message','request failed')))
            return message['result']
        raise ValueError('Codex metadata API timed out')
    try:
        request('initialize',{'clientInfo':{'name':'agterm_utils','version':'0.1.0'}})
        process.stdin.write(b'{"method":"initialized"}\n');process.stdin.flush()
        yield request
    finally:
        selector.close();process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        process.stdout.close()


def codex_history(roots,limit,archived=False):
    roots=[str(Path(r).expanduser().resolve()) for r in roots]
    result=[]
    with codex_server() as request:
        cursor=None
        pages=0
        while len(result)<limit and pages<100:
            pages+=1
            reply=request('thread/list',{'cwd':roots,'limit':min(100,limit-len(result)),
                'sortKey':'updated_at','archived':archived,'cursor':cursor,'useStateDbOnly':True})
            for thread in reply.get('data',[]):
                if str(Path(thread['cwd']).resolve()) not in roots:continue
                result.append({'agent':'codex','conversation_id':thread['id'],'cwd':thread['cwd'],
                               'title':thread.get('name') or 'Codex '+thread['id'][:8],
                               'provider_archived':archived})
            next_cursor=reply.get('nextCursor')
            if not next_cursor or next_cursor==cursor:break
            cursor=next_cursor
    return result[:limit]


def _claude_title(path):
    """Session name Claude stored as metadata (custom-title, else ai-title); never user text."""
    try:
        with path.open('rb') as stream:
            stream.seek(0,2);size=stream.tell();stream.seek(max(0,size-512*1024))
            tail=stream.read().decode('utf-8','replace')
    except OSError:return None
    found={}
    for line in tail.splitlines():
        if '-title"' not in line:continue
        try:entry=json.loads(line)
        except ValueError:continue
        for kind,field in (('custom-title','customTitle'),('ai-title','aiTitle')):
            if entry.get('type')==kind and isinstance(entry.get(field),str) and entry[field].strip():
                found[kind]=' '.join(entry[field].split())[:80]
    return found.get('custom-title') or found.get('ai-title')


def claude_history(roots,limit,home=None):
    home=Path(home or os.environ.get('CLAUDE_CONFIG_DIR',Path.home()/'.claude'))
    candidates=[]
    for root in roots:
        key=re.sub(r'[^A-Za-z0-9-]','-',root)
        directory=home/'projects'/key
        if directory.is_dir():candidates.extend(directory.glob('*.jsonl'))
    roots=[str(Path(r).expanduser().resolve()) for r in roots]
    candidates.sort(key=lambda path:path.stat().st_mtime,reverse=True)
    result=[]
    for path in candidates:
        try:
            ident=str(uuid.UUID(path.stem));cwd=None
            # Only metadata fields in a bounded header; never extract user text.
            with path.open() as stream:
                for _ in range(100):
                    line=stream.readline(512*1024)
                    if not line:break
                    entry=json.loads(line)
                    if entry.get('cwd') and entry.get('sessionId')==ident:
                        cwd=str(Path(entry['cwd']).expanduser().resolve());break
            if cwd not in roots:continue
            title=_claude_title(path) or 'Claude '+ident[:8]
            result.append({'agent':'claude','conversation_id':ident,'cwd':cwd,'title':title})
            if len(result)>=limit:break
        except (OSError,ValueError):continue
    return result


def discover(project,args):
    if args.agent and args.agent not in ('claude','codex'):
        raise ValueError('History discovery is not supported for '+args.agent+'; use resume or session bind')
    from .sessions import records,save,now
    roots={str(Path(r).expanduser().resolve()) for r in project['roots']}
    for record in records().values():
        if record['project']==project['id']:roots.update(str(Path(r).expanduser().resolve()) for r in record['roots'])
    roots=sorted(roots);result=[]
    if not 1<=args.limit<=1000:raise ValueError('--limit must be 1..1000')
    for agent in ([args.agent] if args.agent else ['claude','codex']):
        if agent=='claude':
            if not args.provider_archived:result+=claude_history(roots,args.limit)
        else:
            try:result+=codex_history(roots,args.limit,args.provider_archived)
            except (OSError,ValueError) as exc:
                if args.agent=='codex':raise
                print('Codex history unavailable: '+str(exc),file=__import__('sys').stderr)
    if args.adopt:
        pair=next((r for r in result if r['conversation_id']==args.adopt),None)
        if pair is None:raise ValueError('Requested ID was not discovered under this project; nothing adopted')
        key=str(uuid.uuid5(uuid.NAMESPACE_URL,pair['agent']+':'+pair['conversation_id']))
        existing=next((r for r in records().values() if r.get('conversation_id')==pair['conversation_id'] and r['agent']==pair['agent']),None)
        if existing:print('Already managed: '+existing['id']);return
        record={**pair,'id':key,'project':project['id'],'project_name':project['name'],
            'window':args.selected_window,'roots':[pair['cwd']]+[r for r in project['roots'] if str(Path(r).expanduser().resolve())!=pair['cwd']],
            'agent_args':project.get('agent_args',{}),'terminal_id':None,'hidden':False,'archived':False,
            'status':'imported','created_at':now(),'updated_at':now(),'task':None}
        if not args.dry_run:save(record)
        print(json.dumps(record,indent=2));return
    print(json.dumps(result,indent=2))


def opencode_session_entries(project):
    """Select provider-owned session metadata without exporting transcripts."""
    if len(project['roots'])!=1:raise ValueError('opencode launches require one root')
    cwd=str(Path(project['roots'][0]).expanduser().resolve())
    reply=subprocess.run([shutil.which('opencode') or 'opencode','session','list',
                          '--format','json','--max-count','100'],cwd=cwd,
                         capture_output=True,text=True,check=True,timeout=20)
    rows=json.loads(reply.stdout)
    if not isinstance(rows,list):raise ValueError('Unexpected OpenCode session list format')
    entries=[]
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Unexpected OpenCode session metadata')
        ident=row.get('id')
        if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,255}',ident):
            raise ValueError('Invalid OpenCode session ID in session list')
        # The provider scopes this command to its current project. If it also
        # supplies a directory, exclude other roots rather than guessing.
        directory=row.get('directory')
        if directory and str(Path(directory).expanduser().resolve())!=cwd:continue
        title=row.get('title') or ident
        entries.append((str(title)+' · '+ident,ident))
    return entries


def pick_opencode_session(project):
    from ..cli import pick
    entries=opencode_session_entries(project)
    if not entries:raise ValueError('No OpenCode sessions found for this project')
    return pick('Resume OpenCode',entries)
