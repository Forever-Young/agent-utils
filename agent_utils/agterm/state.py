"""Private local registry. Never stores provider credentials or transcripts."""
from contextlib import contextmanager
import fcntl
import json
import uuid

from ..core.files import atomic_write
from ..core.paths import state_dir  # noqa: F401 (re-exported)




@contextmanager
def locked_registry(name="registry"):
    if name not in ("registry","placement","inbox"):raise ValueError("Unknown state registry")
    directory=state_dir();directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (directory/(name+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        path=directory/(name+'.json')
        data=json.loads(path.read_text()) if path.exists() else ({'version':1,'coordinators':{}} if name=='registry' else {'version':1})
        if data.get('version')!=1:raise ValueError('Unsupported registry version')
        yield data
        atomic_write(path,data)


def coordinator_record(data,project_id):
    return data['coordinators'].get(project_id)


def new_record():
    return {'conversation_id':str(uuid.uuid4()),'terminal_id':None,'agent':'claude'}
