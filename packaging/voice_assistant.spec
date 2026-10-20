# PyInstaller spec: python -m PyInstaller packaging/voice_assistant.spec --noconfirm
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = os.path.dirname(SPECPATH)
datas, binaries, hidden = [], [], []
for pkg in ("sherpa_onnx", "sounddevice", "_sounddevice_data", "sentencepiece", "num2words", "ddgs", "keyring", "mcp", "pycaw", "comtypes", "ytmusicapi"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception as e:  # a package without data files is fine
        print("collect_all skipped", pkg, e)
hidden += collect_submodules("keyring.backends") + collect_submodules("anyio") + ["win32ctypes.core"]

a = Analysis(
    [os.path.join(SPECPATH, "run_app.py")],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "matplotlib", "scipy", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtPdf"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="VoiceAssistant", console=False,
          icon=os.path.join(SPECPATH, "icon.ico"), upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="VoiceAssistant", upx=False)
