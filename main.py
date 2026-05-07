"""Executable entrypoint manual.

Run `python main.py <command> --config <file>` from the repository root. The
full command definitions live in `oracle_auto.cli`; this file only keeps the
project executable without requiring package installation.
"""

from oracle_auto.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
