"""Pairing and restricted-key installation for the native mobile client."""
import base64
import fcntl
import getpass
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.parse import urlencode


def public_key(text):
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] not in ('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256'):
        raise ValueError('Expected an Ed25519, RSA or P-256 OpenSSH public key, without options')
    try:
        raw = base64.b64decode(fields[1], validate=True)
        size = int.from_bytes(raw[:4], 'big')
        if raw[4:4 + size].decode() != fields[0]:
            raise ValueError('Public key type does not match its data')
    except Exception as exc:
        raise ValueError('Invalid public key encoding') from exc
    key = ' '.join(fields[:2])
    with tempfile.NamedTemporaryFile(mode='w', suffix='.pub') as file:
        file.write(key + '\n'); file.flush()
        done = subprocess.run(['ssh-keygen', '-lf', file.name], capture_output=True, text=True)
        if done.returncode:
            raise ValueError('Invalid OpenSSH public key')
    return key


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(key.split()[1])).digest()).decode().rstrip('=')


def pairing(host=None, port=22, user=None, host_key=None):
    paths = [Path(host_key)] if host_key else sorted(Path('/etc/ssh').glob('ssh_host_*_key.pub'), key=lambda p: ('ed25519' not in p.name, str(p)))
    key = next((public_key(p.read_text()) for p in paths if p.is_file()), None)
    if not key:
        raise ValueError('No SSH host public key found; enable Remote Login or pass --host-key PATH')
    fields = {'host': host or socket.gethostname(), 'name': socket.gethostname(), 'port': port,
              'user': user or getpass.getuser(), 'key': key}
    return 'agterm-mobile://pair?' + urlencode(fields), fingerprint(key)


def authorize(text, yes=False, restricted=True, path=None):
    key = public_key(text)
    executable = shutil.which('agents') or str(Path.home() / '.bin/agents')
    if any(c in executable for c in ('"', '\n', '\r', '\\')):
        raise ValueError('Unsafe agents executable path')
    # Absolute executable: sshd forced commands do not inherit the interactive PATH.
    options = 'restrict,pty,command="%s mobile gate" ' % executable if restricted else ''
    entry = options + key + ' agterm-mobile\n'
    if not yes:
        print('Would add to authorized_keys (%s):\n%s' % ('restricted' if restricted else 'full shell', entry.rstrip()))
        return False
    target = Path(path) if path else Path.home() / '.ssh/authorized_keys'
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError('Refusing a symlink authorized_keys')
    lock = target.with_name(target.name + '.agterm-mobile.lock')
    with lock.open('a') as guard:
        os.chmod(lock, 0o600)
        fcntl.flock(guard, fcntl.LOCK_EX)
        previous = target.read_text() if target.exists() else ''
        # Never silently broaden or narrow an existing key's access.
        if any(key in line for line in previous.splitlines()):
            print('Key already present; no changes made')
            return False
        if previous:
            backup = target.with_name(target.name + '.agterm-mobile.bak')
            with backup.open('w') as file:
                os.chmod(backup, 0o600); file.write(previous)
        fd, temporary = tempfile.mkstemp(prefix='.authorized_keys.', dir=target.parent)
        try:
            with os.fdopen(fd, 'w') as file:
                os.fchmod(file.fileno(), 0o600)
                file.write(previous + ('\n' if previous and not previous.endswith('\n') else '') + entry)
                file.flush(); os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)
    print('Authorized %s (%s)' % (fingerprint(key), 'restricted' if restricted else 'full shell'))
    return True


def gate_arguments(command):
    """Parse a tiny allowlist; never invoke the original command through a shell."""
    words = shlex.split(command)
    if words in (['agents', 'mobile', 'serve'],):
        return ['serve']
    if len(words) not in (5, 7) or words[:4] != ['agents', 'mobile', 'attach', '--session']:
        raise ValueError('Restricted key allows only agents mobile serve or attach')
    session = words[4]
    if not session or len(session) > 256 or any(ord(c) < 32 for c in session) or session.startswith('-'):
        raise ValueError('Invalid session id')
    if len(words) == 7 and (words[5] != '--pane' or words[6] not in ('left', 'right')):
        raise ValueError('Invalid pane')
    return words[2:]


def gate():
    args = gate_arguments(os.environ.get('SSH_ORIGINAL_COMMAND', ''))
    # sshd forced commands have a minimal PATH; do not evaluate shell startup files.
    known = [str(Path.home() / '.bin'), str(Path.home() / '.local/bin'),
             '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin', '/bin', '/usr/sbin', '/sbin']
    os.environ['PATH'] = os.pathsep.join(known)
    from . import mobile
    return mobile.command(args)


def appearance(ag):
    answer = ag.call('theme', 'list', json_result=True) or {}
    name = answer.get('theme', 'agterm')
    directory = Path('/Applications/agterm.app/Contents/Resources/ghostty/themes')
    # Only resolve bundled names, never an arbitrary pathname received from config.
    path = directory / name if name and Path(name).name == name else directory / 'agterm'
    if not path.is_file(): path = directory / 'agterm'
    values, palette = {}, {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if '=' not in line: continue
            key, value = (part.strip() for part in line.split('=', 1))
            if key == 'palette' and '=' in value:
                index, color = value.split('=', 1)
                if index.isdigit() and 0 <= int(index) < 16: palette[index] = color
            elif key in ('background', 'foreground', 'cursor-color', 'selection-background', 'selection-foreground'):
                values[key] = value
    return {'theme': name, 'colors': values, 'palette': palette,
            'tints': {'purple': '#d197d9', 'orange': '#ffc66d', 'green': '#a5c261', 'blue': '#2774c7'},
            'density': 'default'}
