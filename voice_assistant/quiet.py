"""Keep the helper programs the assistant starts (PowerShell for media keys, nvidia-smi, ...) from flashing a console window on Windows."""
from __future__ import annotations

import subprocess
import sys


def hide_child_consoles() -> None:
    """Make every subprocess started from here on run without a console window. Safe to call twice; does nothing off Windows."""
    if sys.platform != "win32" or getattr(subprocess.Popen, "_va_quiet", False):
        return
    base = subprocess.Popen

    class QuietPopen(base):
        _va_quiet = True

        def __init__(self, *args, **kwargs):
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    subprocess.Popen = QuietPopen
