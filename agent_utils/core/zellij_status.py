"""Hook script: show an agent's state on its Zellij tab. Usage: zellij_status.py STATE [APP].

`zellij_status.py adopt APP` instead adopts the agent this hook runs under (`agents zellij adopt
--self`), using the hook's session_id as the conversation; for an opt-in SessionStart hook.

STATE is working, blocked, done, error, idle or clear. Reads the hook's JSON from stdin (tool_name
or message become the tab's note). A silent no-op outside Zellij; never raises, always exits 0.
"""
import json
from pathlib import Path
import sys


def main(argv):
    state = argv[1] if len(argv) > 1 else ''
    app = argv[2] if len(argv) > 2 else None
    try:data = json.loads(sys.stdin.read() or '{}')
    except Exception:data = {}
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_utils.core import zellij
    if state == 'adopt':
        if not zellij.in_zellij():return
        from agent_utils.core.zellij_adopt import adopt_self
        from agent_utils.core.paths import config_path
        sid = data.get('session_id') if isinstance(data, dict) and isinstance(data.get('session_id'), str) else None
        adopt_self(str(config_path()), conversation=sid, agent=app)
        return
    note = None
    if isinstance(data, dict):
        note = data.get('message') if isinstance(data.get('message'), str) else (
            ('Needs permission: ' + data['tool_name']) if state == 'blocked' and isinstance(data.get('tool_name'), str) else None)
    zellij.emit_status(state, 'permission' if state == 'blocked' else None, note, app)


if __name__ == '__main__':
    try:main(sys.argv)
    except BaseException:pass
    sys.exit(0)
