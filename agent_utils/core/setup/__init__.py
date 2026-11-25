"""Importable opt-in setup: hooks, launchd jobs, shell startup and the code installer.

Each module has `main(argv=None, ...)` with the same flags as its `scripts/configure-*.py`
wrapper (preview by default, `--apply` writes, `--remove` undoes where it applies). They run
in-process or as `python -m agent_utils.core.setup.NAME`, so no repository folder is needed.
"""
