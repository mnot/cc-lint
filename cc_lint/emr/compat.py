import shlex
import sys
import types


def install_mrjob_pipes_compat() -> None:
    """Provide the removed pipes module for mrjob on Python 3.13."""
    if "pipes" in sys.modules:
        return

    pipes_module = types.ModuleType("pipes")
    setattr(pipes_module, "quote", shlex.quote)
    sys.modules["pipes"] = pipes_module
