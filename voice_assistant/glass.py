"""Windows 11 window backdrop (acrylic = frosted glass, or Mica) behind a translucent Qt window. Silently does nothing where unsupported."""
from __future__ import annotations

import ctypes
import sys

DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWA_SYSTEMBACKDROP_TYPE = 20, 33, 38
BACKDROPS = {"off": 1, "frosted": 3, "mica": 2}  # DWM_SYSTEMBACKDROP_TYPE: none, transient (acrylic), main window (Mica)
MODES = {"off": "Off (solid)", "frosted": "Frosted glass", "mica": "Mica (soft tint)"}


def supported() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= 22621  # the system-backdrop setting arrived in Windows 11 22H2


def apply(hwnd: int, mode: str) -> bool:
    """Set the backdrop for a native window. Returns False when this PC cannot do it."""
    if not supported() or mode not in BACKDROPS:
        return False
    try:
        dwm = ctypes.windll.dwmapi
        for attr, value in ((DWMWA_USE_IMMERSIVE_DARK_MODE, 1), (DWMWA_WINDOW_CORNER_PREFERENCE, 2), (DWMWA_SYSTEMBACKDROP_TYPE, BACKDROPS[mode])):
            v = ctypes.c_int(value)
            dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v))
        m = ctypes.c_int * 4
        margins = m(-1, -1, -1, -1) if mode != "off" else m(0, 0, 0, 0)  # let the backdrop show through the whole client area
        dwm.DwmExtendFrameIntoClientArea(ctypes.c_void_p(hwnd), ctypes.byref(margins))
        return True
    except Exception:
        return False
