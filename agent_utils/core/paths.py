"""External config and state locations, with one-time migration from agterm-utils.

The project used to be called agterm-utils. When the new directory does not exist
and the old one does, the old directory is copied over once. The old directory is
left untouched and serves as the backup.
"""
import os
from pathlib import Path
import shutil

NAME = 'agent-utils'
LEGACY_NAME = 'agterm-utils'


def _resolve(base):
    new, old = base/NAME, base/LEGACY_NAME
    if not new.exists() and old.is_dir():
        shutil.copytree(old, new, symlinks=True)
    return new


def config_dir():
    return _resolve(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config'))))


def state_dir():
    return _resolve(Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local/state'))))


def config_path():
    return config_dir()/'config.json'
