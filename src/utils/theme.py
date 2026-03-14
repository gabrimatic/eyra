"""Shared ANSI color constants for the Eyra terminal UI."""

import sys

_tty = sys.stdout.isatty()

CYAN = "\033[0;36m" if _tty else ""
GREEN = "\033[0;32m" if _tty else ""
YELLOW = "\033[0;33m" if _tty else ""
RED = "\033[0;31m" if _tty else ""
DIM = "\033[2m" if _tty else ""
DIM_ITALIC = "\033[2;3m" if _tty else ""
BOLD = "\033[1m" if _tty else ""
NC = "\033[0m" if _tty else ""  # reset
