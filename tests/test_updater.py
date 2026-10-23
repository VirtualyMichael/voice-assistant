import hashlib
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest

from voice_assistant import __version__, updater

PAYLOAD = b"fake installer bytes" * 1000
SHA = hashlib.sha256(PAYLOAD).hexdigest()
BASE = "https://github.com/VirtualyMichael/voice-assistant/releases/download/v9.9.9/"


def release_json(**over):
    data = {
        "html_url": "https://github.com/VirtualyMichael/voice-assistant/releases/tag/v9.9.9",
        "body": "## What's new",
        "assets": [
            {"name": "VoiceAssistant-Setup-9.9.9.exe", "browser_download_url": BASE + "VoiceAssistant-Setup-9.9.9.exe"},
            {"name": "VoiceAssistant-Setup-9.9.9.exe.sha256", "browser_download_url": BASE + "VoiceAssistant-Setup-9.9.9.exe.sha256"},
        ],
    }
    data.update(over)
    return data


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_versions_compare_numerically_not_as_text():
    assert updater.is_newer("0.10.0", "0.9.10")
    assert updater.is_newer("v1.0", "0.99.99")
    assert not updater.is_newer("0.11.0", "0.11.0")
    assert not updater.is_newer("0.9.9", "0.11.0")
    assert updater.is_newer("0.11.1", "0.11")


def test_a_newer_release_with_installer_and_checksum_is_offered():
    rel = updater.check("0.11.0", client_for(lambda r: httpx.Response(200, json=release_json())))
    assert rel and rel.version == "9.9.9" and rel.installer_name == "VoiceAssistant-Setup-9.9.9.exe"


def test_same_version_or_no_release_offers_nothing():
    assert updater.check("9.9.9", client_for(lambda r: httpx.Response(200, json=release_json()))) is None
    assert updater.check("0.1.0", client_for(lambda r: httpx.Response(404))) is None


def test_release_without_a_checksum_or_from_another_host_is_ignored():
    no_sha = release_json(assets=release_json()["assets"][:1])
    assert updater.check("0.1.0", client_for(lambda r: httpx.Response(200, json=no_sha))) is None
    evil = release_json()
    for a in evil["assets"]:
        a["browser_download_url"] = "https://evil.example.com/" + a["name"]
    assert updater.check("0.1.0", client_for(lambda r: httpx.Response(200, json=evil))) is None


def test_network_failure_is_reported_not_raised_as_a_crash():
    def boom(r):
        raise httpx.ConnectError("offline")

    with pytest.raises(updater.UpdateError):
        updater.check("0.1.0", client_for(boom))


def make_release():
    return updater.Release("9.9.9", "VoiceAssistant-Setup-9.9.9.exe", BASE + "VoiceAssistant-Setup-9.9.9.exe",
                           BASE + "VoiceAssistant-Setup-9.9.9.exe.sha256", "", "https://github.com/x")


def patch_http(monkeypatch, sha_text, payload=PAYLOAD):
    def handler(request):
        if request.url.path.endswith(".sha256"):
            return httpx.Response(200, text=sha_text)
        return httpx.Response(200, content=payload, headers={"content-length": str(len(payload))})

    real = httpx.Client
    monkeypatch.setattr(updater.httpx, "Client", lambda **k: real(transport=httpx.MockTransport(handler), **{x: v for x, v in k.items() if x != "transport"}))


def test_download_verifies_the_checksum_and_reports_progress(monkeypatch, tmp_path):
    patch_http(monkeypatch, f"{SHA}  VoiceAssistant-Setup-9.9.9.exe\n")
    seen = []
    path = updater.download(make_release(), tmp_path, seen.append)
    assert path.read_bytes() == PAYLOAD and seen and seen[-1] == 1.0
    assert not list(tmp_path.glob("*.part"))


def test_a_tampered_download_is_deleted_and_refused(monkeypatch, tmp_path):
    patch_http(monkeypatch, f"{'0' * 64}  x\n")
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.download(make_release(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_unreadable_checksum_and_odd_file_names_are_refused(monkeypatch, tmp_path):
    patch_http(monkeypatch, "not a hash")
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.download(make_release(), tmp_path)
    bad = make_release()
    bad.installer_name = "..\\evil.exe"
    with pytest.raises(updater.UpdateError):
        updater.download(bad, tmp_path)


def test_installer_is_started_detached_and_silent(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda cmd, **k: calls.append((cmd, k)))
    updater.launch_installer(tmp_path / "VoiceAssistant-Setup-9.9.9.exe")
    cmd, kw = calls[0]
    assert "/SILENT" in cmd and "/RELAUNCH=1" in cmd and "/CURRENTUSER" in cmd
    assert kw["creationflags"] & getattr(updater.subprocess, "DETACHED_PROCESS", 0) == getattr(updater.subprocess, "DETACHED_PROCESS", 0)


def test_running_from_source_cannot_self_update():
    assert "source" in updater.can_self_update()


def test_the_version_is_shown_in_the_top_bar_and_window_title(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    import voice_assistant.app as A
    from voice_assistant.config import Config

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True, check_updates=False))
    try:
        assert w.version_btn.text() == f"v{__version__}"
        assert __version__ in w.windowTitle()
        rel = make_release()
        w._offer_update = lambda r: None  # no modal dialog in tests
        w._on_update_found(rel, False)
        assert "9.9.9" in w.version_btn.text() and w.version_btn.property("update") is True
    finally:
        w.close()
