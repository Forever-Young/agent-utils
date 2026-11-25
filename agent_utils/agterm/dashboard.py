"""Session snapshots shared by Textual, fzf and numbered menus."""

def dashboard(args,ag):
    """One-shot snapshot selector; refresh explicitly rather than inferring status."""
    from .sessions import live_rows,records
    from ..cli import main,pick,AGENT_NAMES
    show_all=args.all
    while True:
        saved=records();by_terminal={r['terminal_id']:r for r in saved.values()}
        rows=live_rows(ag,args.window)
        entries=[]
        for row in rows:
            record=by_terminal.get(row['id'])
            if record and not show_all and (record.get('hidden') or record.get('archived')):continue
            if args.project and (not record or record['project']!=args.project):continue
            status=row.get('status','idle')
            label=f"[{status}] {row['window_name']} / {row['workspace_name']} / {row.get('name','')}"
            entries.append((status,record.get('updated_at','') if record else '',label,('live',row,record)))
        for record in saved.values():
            if any(r['id']==record['terminal_id'] for r in rows):continue
            if args.window and record['window']!=args.window:continue
            if args.project and record['project']!=args.project:continue
            if not show_all and (record.get('hidden') or record.get('archived')):continue
            entries.append(('closed',record['updated_at'],f"[closed · {AGENT_NAMES.get(record['agent'],record['agent'])}] {record['window']} / {record['title']}",('saved',None,record)))
        entries.sort(key=lambda e:e[1],reverse=True)
        entries.sort(key=lambda e:e[0] not in ('blocked','completed'))
        choice=pick('Sessions · attention first · snapshot',[(e[2],e[3]) for e in entries]+[('Refresh','refresh'),('Toggle hidden / archived','all')])
        if choice is None:return
        if choice=='refresh':continue
        if choice=='all':show_all=not show_all;continue
        kind,row,record=choice
        actions=[]
        if row:actions.append(('Focus terminal','focus'))
        if record:
            actions.extend([('Show saved details','details'),('Hide / unhide in utility menus','unhide' if record.get('hidden') else 'hide'),('Archive / unarchive utility record','unarchive' if record.get('archived') else 'archive')])
            if not row:actions.append(('Resume saved conversation (backend must be stopped)','resume'))
        action=pick(record['title'] if record else row.get('name','Session'),actions)
        if action is None:continue
        if action=='focus':ag.call('session','select','--target',row['id'],'--window',row['window_id']);return
        if action=='details':
            import json
            print(json.dumps(record,indent=2));return
        command=['--config',args.config,'session',action,'--record',record['id']]
        if action=='resume':
            stopped=pick('Has the previous provider process ended?', [('Cancel',False),('Yes, resume the saved conversation',True)])
            if not stopped:continue
            command+=['--backend-stopped']
        main(command)
