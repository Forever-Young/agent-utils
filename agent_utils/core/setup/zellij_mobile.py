"""Install the contributed mobile Zellij config, preview first; preserve custom files."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import tempfile

SOURCE = Path(__file__).resolve().parents[2]/'contrib/zellij/mobile.kdl'


def destination(home):
    config = home/'.config/agent-utils/config.json'
    data = json.loads(config.read_text()) if config.is_file() else {}
    value = data.get('zellij', {}).get('ssh_config_path', '~/.config/zellij/config-ssh.kdl')
    if not value:raise ValueError('Set zellij.ssh_config_path before installing the mobile config')
    path = home/value[2:] if value.startswith('~/') else Path(value)
    if not path.is_absolute():raise ValueError('zellij.ssh_config_path must be absolute or start with ~/')
    return path


def status(home):
    try:
        path = destination(home)
        return path.is_file() and path.read_text() == SOURCE.read_text()
    except (OSError, ValueError):return False


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--remove', action='store_true')
    p.add_argument('--home', type=Path, default=Path.home())
    a = p.parse_args(argv)
    path = destination(a.home.expanduser()); text = SOURCE.read_text()
    print(('Remove: ' if a.remove else 'Install: ') + str(path))
    if not a.apply:
        print(text);print('Preview only; add --apply. Custom files are never overwritten.');return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent/(path.name+'.agent-utils.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink():raise ValueError('Refusing symlink: ' + str(path))
        if path.exists() and path.read_text() != text:
            raise ValueError('Preserving customized config: ' + str(path))
        if a.remove:
            if path.exists():path.unlink()
        elif not path.exists():
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.zellij-mobile-')
            try:
                with os.fdopen(fd, 'w') as stream:stream.write(text)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):os.unlink(temporary)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
