#!/usr/bin/env python3
"""List and apply the optional configurations. Thin wrapper: the code is agent_utils.core.setup.optional."""
import sys
from pathlib import Path

try:
    import agent_utils  # noqa: F401
except ImportError:  # run from a checkout without installation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_utils.core.setup.optional import main

if __name__ == '__main__':
    raise SystemExit(main())
