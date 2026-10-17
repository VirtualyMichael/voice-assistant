"""Entry point for the packaged app (PyInstaller)."""
import sys

from voice_assistant.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
