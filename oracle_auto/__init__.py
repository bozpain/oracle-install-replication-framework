"""Package metadata manual.

The runtime implementation is split across config, precheck, phase builders,
runner, report, executor, and state modules. Keep package-level exports minimal
so automation behavior remains explicit in the CLI and phase modules.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
