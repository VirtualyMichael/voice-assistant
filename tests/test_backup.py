import json
import zipfile

import pytest

from voice_assistant.backup import make_backup, restore_backup


def make_home(root):
    (root / "chats").mkdir(parents=True)
    (root / "chats" / "a.json").write_text('{"title": "one"}', encoding="utf-8")
    (root / "chats" / "b.json").write_text('{"title": "two"}', encoding="utf-8")
    (root / "config.json").write_text('{"voice": "x"}', encoding="utf-8")
    (root / "notes.json").write_text("[]", encoding="utf-8")
    (root / "remote-devices.json").write_text('{"token": "secret"}', encoding="utf-8")
    (root / "models").mkdir()
    (root / "models" / "big.bin").write_bytes(b"0" * 100)


def test_backup_holds_chats_and_settings_but_not_phone_tokens_or_models(tmp_path):
    home = tmp_path / "home"
    make_home(home)
    out = tmp_path / "b.zip"
    assert make_backup(home, out, "0.16.0") == {"files": 2, "chats": 2}
    names = set(zipfile.ZipFile(out).namelist())
    assert names == {"backup.json", "config.json", "notes.json", "chats/a.json", "chats/b.json"}
    assert "secret" not in b"".join(zipfile.ZipFile(out).read(n) for n in names).decode("utf-8", "ignore")


def test_restore_puts_things_back_and_keeps_what_it_replaced(tmp_path):
    home = tmp_path / "home"
    make_home(home)
    out = tmp_path / "b.zip"
    make_backup(home, out)
    (home / "chats" / "a.json").write_text('{"title": "changed"}', encoding="utf-8")
    (home / "chats" / "b.json").unlink()
    result = restore_backup(out, home)
    assert json.loads((home / "chats" / "a.json").read_text(encoding="utf-8"))["title"] == "one"
    assert (home / "chats" / "b.json").exists()
    assert (home / "chats" / "a.json.before-restore").read_text(encoding="utf-8") == '{"title": "changed"}'
    assert result["restored"] == 4 and result["kept"] >= 1


def test_restore_refuses_other_zips_and_ignores_paths_that_escape(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(ValueError):
        restore_backup(bad, tmp_path / "t")
    with pytest.raises(ValueError):
        restore_backup(tmp_path / "missing.zip", tmp_path / "t")
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("backup.json", json.dumps({"app": "voice-assistant"}))
        z.writestr("../outside.txt", "x")
        z.writestr("chats/../../outside2.json", "x")
        z.writestr("remote-devices.json", "{}")
        z.writestr("chats/ok.json", "{}")
    target = tmp_path / "t"
    assert restore_backup(evil, target)["restored"] == 1
    assert (target / "chats" / "ok.json").exists()
    assert not (tmp_path / "outside.txt").exists() and not (tmp_path / "outside2.json").exists() and not (target / "remote-devices.json").exists()
