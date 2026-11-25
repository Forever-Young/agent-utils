"""Where the installed agent_utils package lives, for hook commands written by the setup modules."""
import importlib.util
from pathlib import Path


def package_dir(home):
    """The scripts/install.py copy when present, else the package this Python can import
    (a Homebrew install), else the install.py location so previews show the expected path."""
    default = Path(home) / '.bin/agterm-tools/agent_utils'
    if default.is_dir():
        return default
    spec = importlib.util.find_spec('agent_utils')
    if spec and spec.origin:
        return Path(spec.origin).parent
    return default
