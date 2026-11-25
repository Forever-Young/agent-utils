"""Atomic JSON writes shared by configuration and state."""
import json
import os
from pathlib import Path
import tempfile


def atomic_write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.config-')
    try:
        with os.fdopen(fd,'w') as stream: json.dump(data,stream,indent=2);stream.write('\n')
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
