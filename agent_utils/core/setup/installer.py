"""Preview-first installation and code rollback; never edits project/shell config.

Run from a repository checkout (`python3 scripts/install.py`) or as
`python -m agent_utils.core.setup.installer`. From the installed copy, the source folder
recorded in the install manifest (or `--source`) is what gets installed."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv


def brew_managed():
    """Path of an `agents` on PATH that Homebrew installed, else None."""
    found=shutil.which('agents')
    if found and '/Cellar/' in os.path.realpath(found):return found
    return None


def stamp():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def targets(dest):
    # 'package' keeps its label for the pre-rename agterm_utils package so old snapshots still restore.
    return {'package':dest/'agterm-tools/agterm_utils','package-v2':dest/'agterm-tools/agent_utils','project-session':dest/'project-session','agents':dest/'agents','manifest':dest/'agterm-tools/install-manifest.json'}


def snapshot(dest):
    backup=dest/'agterm-tools/backups'/stamp();backup.mkdir(parents=True)
    for label,path in targets(dest).items():
        if path.is_symlink():raise ValueError('Refusing managed symlink: '+str(path))
        if path.is_dir():shutil.copytree(path,backup/label)
        elif path.exists():shutil.copy2(path,backup/label)
    return backup


def replace(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    # Stage beside destination so final replacement stays on the same filesystem.
    with tempfile.TemporaryDirectory(dir=target.parent,prefix='.install-') as folder:
        staged=Path(folder)/target.name
        if source.is_dir():shutil.copytree(source,staged,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        else:shutil.copy2(source,staged)
        if target.is_dir():shutil.rmtree(target)
        os.replace(staged,target)


def find_source(helpers,given=None):
    """Folder that holds the `agent_utils` package to install: --source, else the running package's
    parent, else (when running from the installed copy itself) the source the manifest recorded."""
    if given:
        root=Path(given).expanduser().resolve()
    else:
        root=Path(__file__).resolve().parents[3]
    installed=(helpers/'agent_utils')
    if installed.exists() and (root/'agent_utils').resolve()==installed.resolve():
        try:recorded=Path(json.loads((helpers/'install-manifest.json').read_text()).get('source',''))
        except (OSError,ValueError):recorded=None
        if not recorded or not (recorded/'agent_utils').is_dir() or recorded.resolve()==root.resolve():
            raise SystemExit('This is the installed copy and no source checkout is recorded. Run scripts/install.py from the repository, or pass --source.')
        root=recorded
    if not (root/'agent_utils').is_dir():raise SystemExit('No agent_utils package in '+str(root))
    return root


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true');p.add_argument('--bin-dir',type=Path,default=Path.home()/'.bin')
    p.add_argument('--with-tui',action='store_true',help='Install pinned Textual in an isolated runtime')
    p.add_argument('--with-pdf',action='store_true',help='Also install reportlab (PDF cheatsheet) in the isolated runtime')
    p.add_argument('--rollback',nargs='?',const='latest',help='Restore a code snapshot (latest or timestamp)')
    p.add_argument('--list-backups',action='store_true')
    p.add_argument('--source',type=Path,help='Folder containing agent_utils (default: this checkout)')
    args=p.parse_args(argv);dest=args.bin_dir.expanduser().resolve();helpers=dest/'agterm-tools'
    backups=helpers/'backups'
    if args.list_backups:
        for path in sorted(backups.glob('*')):print(path.name)
        return
    brewed=brew_managed()
    if brewed:print('Warning: agents on PATH is a Homebrew install ('+brewed+'). Use one channel per machine; this would add a second copy.')
    print('Package:',helpers/'agent_utils');print('Command:',dest/'agents')
    print('Project config, histories, shell and terminal configuration will not be modified.')
    print('Optional configurations (Kiro hooks, inbox relay, ...): agents admin config run optional-list')
    selected=None
    if args.rollback:
        available=sorted(path for path in backups.glob('*') if ((path/'package').is_dir() or (path/'package-v2').is_dir()) and (path/'agents').is_file())
        selected=available[-1] if args.rollback=='latest' and available else next((b for b in available if b.name==args.rollback),None)
        if selected is None:raise SystemExit('No complete rollback snapshot found')
        print('Restore:',selected.name)
    elif args.with_tui or args.with_pdf:print('Isolated runtime (Textual 8.2.8 with --with-tui, reportlab 4.5.1 with --with-pdf):',helpers/'runtime')
    if not args.apply:return
    source=None if selected else find_source(helpers,args.source)
    # Resolve/install dependencies before replacing working commands.
    runtime=helpers/'runtime'
    if (args.with_tui or args.with_pdf) and not selected:
        if sys.version_info<(3,9):raise SystemExit('Python 3.9+ required')
        if not (runtime/'bin/python').exists():venv.create(runtime,with_pip=True)
        subprocess.run([str(runtime/'bin/python'),'-m','pip','install',*(['textual==8.2.8'] if args.with_tui else []),*(['reportlab==4.5.1'] if args.with_pdf else [])],check=True)
    backup=snapshot(dest)
    print('Backup:',backup.name)
    if selected:
        for label,path in targets(dest).items():
            source=selected/label
            if source.exists():replace(source,path)
        print('Restored code. Configuration and provider histories are unchanged.');return
    replace(source/'agent_utils',helpers/'agent_utils')
    legacy=helpers/'agterm_utils'
    if legacy.is_dir():
        # Kiro hooks written before the rename point at the old path; keep a forwarder there.
        shutil.rmtree(legacy);legacy.mkdir()
        # Also the runner and gate: agterm's captured launch commands and live coordinators name these paths.
        for module in ('kiro_hook','runner','coordinator_gate'):
            (legacy/(module+'.py')).write_text('import runpy,sys\nsys.path.insert(0,'+repr(str(helpers))+')\n'
                'runpy.run_module("agent_utils.agterm.'+module+'",run_name="__main__")\n')
    for old in ('project-session','agent-utils'):
        # Removed names: project-session imports functions that no longer exist. Snapshot keeps it.
        if (dest/old).exists():(dest/old).unlink()
    for name,module,func in [('agents','entry','entrypoint')]:
        wrapper=dest/name
        code='#!/usr/bin/env python3\nimport os, sys\n'
        if (runtime/'bin/python').exists():
            code+='runtime = '+repr(str(runtime))+'\n'
            code+='if sys.prefix != runtime:\n    os.execv(runtime + "/bin/python", [runtime + "/bin/python", __file__, *sys.argv[1:]])\n'
        code+='sys.path.insert(0, '+repr(str(helpers))+')\nfrom agent_utils.'+module+' import '+func+'\n'+func+'()\n'
        fd,tmp=tempfile.mkstemp(dir=dest,prefix='.wrapper-')
        with os.fdopen(fd,'w') as stream:stream.write(code)
        os.chmod(tmp,0o755);os.replace(tmp,wrapper)
    manifest={'version':1,'installed_at':stamp(),'backup':backup.name,'bin_dir':str(dest),'helpers':str(helpers),'source':str(source)}
    (helpers/'install-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Installed. Existing external configuration and histories are preserved.')


if __name__=='__main__':main()
