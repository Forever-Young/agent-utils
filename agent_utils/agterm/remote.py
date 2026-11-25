"""Remote selection through agterm's native, revalidating attach API."""
import argparse
import json
import os
import re
from pathlib import Path
import shlex
import subprocess

ATTENTION={'blocked','completed'}


def valid_host(host):
    if not host or host.startswith('-') or any(c.isspace() or ord(c)<32 for c in host):
        raise ValueError('Use an SSH destination/alias without whitespace or a leading dash')
    return host


def inventory(ag,host=None):
    args=['zmx','tree']+([valid_host(host)] if host else [])
    reply=ag.call(*args,json_result=True) or {}
    return reply.get('remote',reply)


def filter_sessions(rows,args):
    from ..cli import AGENTS
    result=[]
    for row in rows:
        if args.window and row.get('windowName')!=args.window:continue
        if args.project and row.get('workspaceName')!=args.project:continue
        if args.attention and row.get('status') not in ATTENTION:continue
        if args.agents_only:
            foreground=' '.join(str(p.get('foreground','')) for p in row.get('panes',[]))
            pattern=r'(?<![A-Za-z0-9_-])(?:'+'|'.join(re.escape(x) for x in AGENTS)+r')(?![A-Za-z0-9_-])'
            if not re.search(pattern,foreground.lower()):continue
        result.append(row)
    if args.attention_first:
        result.sort(key=lambda row:row.get('status') not in ATTENTION)
    return result


def terminal_attach(ag,args):
    """Attach one existing origin pane from an SSH terminal, with a best-effort guard."""
    from ..cli import pick
    if args.host or args.destination_window or args.companions:
        raise ValueError('Run terminal attachment on the origin Mac after SSH; --host, --destination-window and --companions apply only to native Mac attachment')
    if args.dry_run:
        print('Would discover local Live sessions, revalidate the selected pane, check its bundled zmx daemon, then attach. The check/attach race remains.')
        return
    if not os.isatty(0) or not os.isatty(1):raise ValueError('Terminal attachment requires a TTY; use ssh -t or a Termius interactive shell')
    original=inventory(ag)
    rows=filter_sessions(original.get('sessions',[]),args)
    if args.session:
        selected=next((row for row in rows if row['id']==args.session),None)
        if selected is None:raise ValueError('Session is absent or excluded by filters; nothing attached')
    else:
        if not rows:raise ValueError('No matching local Live sessions; agterm must be running on this Mac')
        selected=pick('Live session · terminal attach',[(f"{r.get('windowName','')} / {r.get('workspaceName','')} / {r.get('name','')} [{r.get('status','idle')}]",r) for r in rows])
        if selected is None:return
    panes=selected.get('panes',[])
    if args.pane:
        pane=next((p for p in panes if p.get('pane')==args.pane),None)
        if pane is None:raise ValueError('Selected session does not have that pane')
    elif len(panes)==1:pane=panes[0]
    elif panes:
        pane=pick('Pane · one terminal per connection',[(p['pane'],p) for p in panes])
        if pane is None:return
    else:raise ValueError('Selected row has no attachable terminal pane')
    # Re-read after the interactive picker, which can stay open indefinitely.
    return attach_pane(ag,original,selected['id'],pane,args)


def attach_pane(ag,original,session_id,pane,args=None,notice=True):
    """Re-read the inventory, refuse any change since ORIGINAL, check the daemon, attach.

    Shared by `agents remote terminal` and `agents mobile attach`. The caller must
    already hold a TTY. Returns the exit code of the bundled zmx client."""
    fresh=inventory(ag)
    rows=fresh.get('sessions',[])
    if args is not None:rows=filter_sessions(rows,args)
    row=next((r for r in rows if r.get('id')==session_id),None)
    current=next((p for p in (row or {}).get('panes',[]) if p.get('pane')==pane.get('pane')),None)
    if not current or current.get('daemon')!=pane.get('daemon') or original.get('endpoint')!=fresh.get('endpoint'):
        raise ValueError('Session/pane or bundled endpoint changed; nothing attached. Select again.')
    endpoint=fresh.get('endpoint',{})
    executable=Path(endpoint.get('executable',''))
    directory=Path(endpoint.get('socketDirectory',''))
    daemon=current.get('daemon')
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable,os.X_OK) or not directory.is_absolute():
        raise ValueError('Origin did not provide an available absolute bundled zmx endpoint')
    if not isinstance(daemon,str) or not daemon or daemon.startswith('-') or '/' in daemon or any(ord(c)<32 for c in daemon):
        raise ValueError('Origin returned an invalid daemon name')
    # Avoid inherited names/prefixes selecting a different daemon, managed-client
    # leadership, or a disabled detach key. Leave the origin process environment alone.
    env={k:v for k,v in os.environ.items() if not k.startswith(('ZMX_','AGTERM_','AGT_'))}
    env['ZMX_DIR']=str(directory)
    if notice:print('Ctrl+\\ detaches this client only. A daemon disappearing between the existence check and attach can still be recreated by zmx.',flush=True)
    checked=subprocess.run([str(executable),'get',daemon],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=10)
    if checked.returncode:raise ValueError('Selected daemon is no longer available; nothing attached')
    # No blocking UI or additional subprocess between the check and attach. zmx
    # lacks existing-only attach, so this cannot be made atomic in this wrapper.
    return subprocess.call([str(executable),'attach',daemon],env=env)


def ssh_menu(ag, args):
    """Offer launch as well as attach from a terminal on the origin Mac."""
    from ..cli import load, pick, projects, main as launch_main
    choice = pick('SSH sessions', [('Attach an existing agterm session', 'terminal'),
                                  ('Start or resume an agterm session', 'new'),
                                  ('Start or attach a Zellij session', 'zellij')])
    if choice is None:return
    if choice == 'terminal':return terminal_attach(ag, args)
    if choice == 'zellij':
        from ..core.zellij import command
        return command(['menu', '--config', args.config] + (['--dry-run'] if args.dry_run else []))
    pairs = [(w, p) for w, p in projects(load(args.config))
             if (not args.window or w['name'] == args.window)
             and (not args.project or p['name'] == args.project or p['id'] == args.project)]
    windows = list(dict.fromkeys(w['name'] for w, p in pairs))
    window = pick('Window', [(name, name) for name in windows])
    if window is None:return
    project = pick('Project', [(p['name'], p) for w, p in pairs if w['name'] == window])
    if project is None:return
    argv = ['--config', args.config] + (['--dry-run'] if args.dry_run else [])
    record = launch_main(argv + ['menu', '--window', window, '--project', project['id']])
    if not isinstance(record, dict) or not record.get('terminal_id'):return
    # Reuse the normal fresh-inventory / endpoint checks for this exact new row.
    from argparse import Namespace
    attach_args = Namespace(**vars(args))
    attach_args.session = record['terminal_id']
    attach_args.window = attach_args.project = None
    return terminal_attach(ag, attach_args)


def main(argv=None):
    from ..cli import BACK, Agterm, config_path, load, pick
    p=argparse.ArgumentParser(description='Choose an SSH shell or attach an existing agterm session on another Mac')
    p.add_argument('action',nargs='?',default='menu',choices=['menu','list','attach','terminal','shell','detach','lead'])
    p.add_argument('--config',default=str(config_path()));p.add_argument('--host')
    p.add_argument('--window',help='Origin window name filter');p.add_argument('--project',help='Origin workspace name filter')
    p.add_argument('--destination-window',help='Local window ID for attachment')
    p.add_argument('--session',help='Exact origin session ID (local row ID for detach/lead)')
    p.add_argument('--attention',action='store_true');p.add_argument('--attention-first',action='store_true')
    p.add_argument('--agents-only',action='store_true');p.add_argument('--companions',action='store_true')
    p.add_argument('--pane',choices=['left','right'],help='Origin pane for terminal-only attachment; otherwise choose when split')
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args(argv);ag=Agterm(args.dry_run)
    if args.action=='menu' and not args.host and (os.environ.get('SSH_TTY') or os.environ.get('SSH_CONNECTION')):
        return ssh_menu(ag,args)
    if args.action=='terminal':return terminal_attach(ag,args)
    if args.pane:raise ValueError('--pane applies to terminal attachment only')
    if args.action in ('detach','lead'):
        if not args.session:raise ValueError('Specify --session with the local row ID')
        # Refuse closing an origin row: a remote close detaches this client only.
        if not args.dry_run:
            tree=ag.call('tree','--window',args.destination_window or 'active',json_result=True)
            tree=tree.get('tree',tree)
            row=next((s for w in tree.get('workspaces',[]) for s in w.get('sessions',[]) if s['id']==args.session),None)
            if not row or not row.get('remoteHost'):raise ValueError('Select an attached remote row in the destination window')
        return ag.call('session','close' if args.action=='detach' else 'lead','--target',args.session,'--window',args.destination_window or 'active')
    if not args.host and args.action!='list':
        data=load(args.config) if os.path.exists(args.config) else {}
        hosts=data.get('remotes',[])
        if not hosts:raise ValueError('Specify --host SSH_ALIAS or add remotes to config.json')
        asked_host=True
    else:asked_host=False
    while True:
        if asked_host:
            args.host=pick('Remote host',[(h.get('name',h['host']),h['host']) for h in hosts])
            if args.host is None:return
        if args.action!='menu':break
        choice=pick('Connect',[('Existing agterm session','attach'),('Ordinary SSH shell','shell')],back=asked_host)
        if choice is None:return
        if choice is BACK:continue
        args.action=choice;break
    if args.action=='shell':
        command=['ssh',valid_host(args.host)]
        if args.dry_run:print(shlex.join(command));return
        env={k:v for k,v in os.environ.items() if not k.startswith(('AGTERM_','AGT_'))}
        return subprocess.call(command,env=env)
    if args.dry_run:
        ag.call('zmx','tree',*([valid_host(args.host)] if args.host else []),json_result=True)
        if args.action=='attach' and args.session:
            command=['zmx','attach',args.host,args.session]
            if args.destination_window:command+=['--window',args.destination_window]
            if args.companions:command+=['--companions']
            ag.call(*command)
        return
    rows=filter_sessions(inventory(ag,args.host).get('sessions',[]),args)
    if args.action=='list':print(json.dumps(rows,indent=2));return
    if args.session:
        selected=next((row for row in rows if row['id']==args.session),None)
        if selected is None:raise ValueError('Session is absent or excluded by filters; nothing attached')
    else:
        if not rows:raise ValueError('No matching live sessions; check origin Live mode and filters')
        selected=pick('Remote session',[(f"{r.get('windowName','')} / {r.get('workspaceName','')} / {r.get('name','')} [{r.get('status','idle')}]",r) for r in rows])
        if selected is None:return
    command=['zmx','attach',valid_host(args.host),selected['id']]
    if args.destination_window:command+=['--window',args.destination_window]
    if args.companions:command+=['--companions']
    # The native API re-resolves the origin and its bundled endpoint before attach.
    return ag.call(*command)


def entrypoint():
    from ..cli import guarded
    guarded(main)
