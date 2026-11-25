"""Track a managed provider's process and exit without editing its history."""
import os
from pathlib import Path
import signal
import subprocess
import sys


def wrapped(record_id, argv):
    return ['env', 'AGTERM_UTILS_RECORD=' + record_id, sys.executable,
            str(Path(__file__).resolve()), record_id, '--', *argv]


def update(record_id, **changes):
    from agent_utils.agterm.state import locked_registry
    from agent_utils.agterm.sessions import now
    with locked_registry() as registry:
        record = registry.get('sessions', {}).get(record_id)
        if record is not None:record.update(updated_at=now(), **changes)


def replay_command(key, command):
    """A replayed launch (agterm re-running its captured command) resumes the recorded
    conversation instead of starting a new one. The first start is left unchanged."""
    try:
        from agent_utils.agterm.state import locked_registry
        with locked_registry() as registry:
            record = dict(registry.get('sessions', {}).get(key) or {})
        # A Zellij-resurrected pane may start in another folder; run from the recorded one.
        if record.get('zellij') and record.get('cwd') and os.path.isdir(record['cwd']):os.chdir(record['cwd'])
        if (record.get('conversation_id') and record.get('status') not in (None, 'launching', 'adopted')
                and command and os.path.basename(command[0]) == record.get('agent')):
            from agent_utils.agterm.restore import resume_argv
            from agent_utils.cli import config_path
            resumed = resume_argv(record, str(config_path()))
            if resumed:
                # The replay starts in the pane's last directory (often $HOME). Resume from the
                # recorded one so the provider opens its own workspace and trust settings.
                directory = record.get('cwd') or record['roots'][0]
                if os.path.isdir(directory):os.chdir(directory)
                return resumed
    except Exception:  # never block a launch on the replay lookup
        pass
    return command


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    key, separator, *command = argv
    if separator != '--' or not command:raise ValueError('runner requires RECORD -- COMMAND')
    from agent_utils.agterm.workers import process_birth
    from agent_utils.agterm.sessions import now
    command = replay_command(key, command)
    try:
        child = subprocess.Popen(command)
    except OSError:
        update(key, status='failed', exit_code=127, exited_at=now())
        raise
    # Terminal SIGINT already reaches both processes in their foreground group.
    # Forwarding it would turn one Ctrl-C into two for the provider.
    signal.signal(signal.SIGINT, lambda sig, frame: None)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, lambda sig, frame: child.send_signal(sig) if child.poll() is None else None)
    extra = {}
    try:
        # In a Zellij pane: record where this provider runs. A resurrected session renumbers
        # tab and pane IDs, and this runs again when the pane is restarted, so the record heals.
        from agent_utils.core.zellij import locate_self
        found = locate_self()
        if found:extra['zellij'] = {'session': found[0], 'tab_id': found[1], 'pane_id': found[2], 'resumable': True}
    except Exception:
        pass
    update(key, provider_pid=child.pid, provider_birth=process_birth(child.pid),
           status='running', last_seen=now(), exit_code=None, exited_at=None, **extra)
    code = child.wait()
    update(key, exit_code=code, exited_at=now(), status='completed' if code == 0 else 'failed')
    return code if code >= 0 else 128 - code


if __name__ == '__main__':
    # This file is invoked by absolute path so it also works from any worker cwd.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    raise SystemExit(main())
