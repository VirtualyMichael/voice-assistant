"""`VoiceAssistant.exe --selftest [result.json]`: proves a packaged build contains everything it needs."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

MODULES = [
    "PySide6.QtWidgets", "sherpa_onnx", "sounddevice", "numpy", "keyboard", "httpx", "sentencepiece", "psutil", "ddgs",
    "keyring", "num2words", "mcp", "mcp.client.stdio",
    "voice_assistant.app", "voice_assistant.session", "voice_assistant.agent_tools", "voice_assistant.mcp_client",
    "voice_assistant.setup_wizard", "voice_assistant.providers", "voice_assistant.memory",
]


def run(out: Path | None = None) -> int:
    results: dict[str, str] = {}
    for name in MODULES:
        try:
            importlib.import_module(name)
            results[name] = "ok"
        except Exception as e:
            results[name] = f"FAILED: {e.__class__.__name__}: {e}"

    def check(label, fn):
        try:
            results[label] = f"ok: {fn()}"
        except Exception as e:
            results[label] = f"FAILED: {e.__class__.__name__}: {e}"

    def audio():
        from voice_assistant.audio_io import list_devices

        return f"{len(list_devices('input'))} inputs, {len(list_devices('output'))} outputs"

    def keyring_backend():
        import keyring

        return keyring.get_keyring().__class__.__name__

    def speech_text():
        from voice_assistant.chunker import clean_for_speech

        return clean_for_speech("It costs $5.20 at 3:45 pm")

    def bpe():
        import sentencepiece

        return sentencepiece.__version__

    check("audio devices", audio)
    check("credential store", keyring_backend)
    check("text normaliser", speech_text)
    check("sentencepiece", bpe)
    failed = [k for k, v in results.items() if v.startswith("FAILED")]
    text = json.dumps({"ok": not failed, "frozen": bool(getattr(sys, "frozen", False)), "results": results}, indent=1)
    if out:
        out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 1 if failed else 0
