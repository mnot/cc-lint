"""
Monkeypatches for compatibility.
"""
import sys
import shlex
import types

# Monkeypatch pipes for Python 3.13 (mrjob compatibility)
if sys.version_info >= (3, 13):
    if "pipes" not in sys.modules:
        pipes = types.ModuleType("pipes")
        pipes.quote = shlex.quote  # type: ignore
        sys.modules["pipes"] = pipes
