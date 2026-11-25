"""One-time placement of new rows; preserve the order of already-known rows."""
import subprocess
from .state import locked_registry


def remember_existing(tree):
    """Bootstrap baselines existing rows without moving any of them."""
    rows=[s['id'] for w in tree.get('workspaces',[]) for s in w.get('sessions',[])]
    if not rows:return
    with locked_registry('placement') as registry:
        seen=registry.setdefault('placement_seen',[])
        for ident in rows:
            if ident not in seen:seen.append(ident)


def place_once(ag,window_id,session_id,position):
    if position!='top' or not window_id or not session_id:return
    if ag.dry:
        return ag.call('session','move','--to','top','--target',session_id,'--window',window_id)
    try:
        with locked_registry('placement') as registry:
            seen=registry.setdefault('placement_seen',[])
            if session_id in seen:return
            ag.call('session','move','--to','top','--target',session_id,'--window',window_id)
            seen.append(session_id)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        # A row already exists. Do not turn a placement failure into a failed
        # launch that encourages the caller to create a duplicate agent.
        print('Session is open; moving it to the top failed: '+str(exc))
