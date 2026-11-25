"""Entry point pinned into Zellij launcher panes, independent of shell PATH."""
import sys
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from agent_utils.core.zellij import command
    raise SystemExit(command(['launcher'] + sys.argv[1:]))
