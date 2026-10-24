import json

from voice_assistant import evaluate as ev


def test_every_case_is_well_formed():
    cases = json.loads(ev.CASES_FILE.read_text(encoding="utf-8"))
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) and len(cases) >= 25
    for c in cases:
        assert c["question"] and c["category"] and (c.get("expect") or c.get("tools") or c.get("not_tools")), c["id"]


def test_answers_are_checked_by_words_and_tool_calls():
    case = {"expect": {"all": ["23.47"], "any": ["total", "spent"], "none": ["cannot"]},
            "tools": [{"name": "phone_set_alarm", "args": {"time": "^0?6:30"}}], "not_tools": ["change_setting"]}
    good = [("phone_set_alarm", {"time": "06:30"})]
    assert ev.check(case, "You spent 23.47 in total.", good) == []
    problems = ev.check(case, "I cannot tell.", [("phone_set_alarm", {"time": "7:00"}), ("change_setting", {"key": "x"})])
    text = " | ".join(problems)
    assert "23.47" in text and "one of" in text and "not say 'cannot'" in text and "expected" in text and "should not have called change_setting" in text


def test_forbidden_calls_can_be_narrowed_to_some_arguments():
    case = {"not_tools": ["change_setting"], "not_tools_args": {"change_setting": {"key": "allow_web"}}}
    assert ev.check(case, "", [("change_setting", {"key": "tts_speed"})]) == []
    assert ev.check(case, "", [("change_setting", {"key": "allow_web"})])


def test_the_test_pictures_and_pdf_are_drawn(tmp_path):
    import os
    import subprocess
    import sys

    # its own process with the normal Qt platform: the headless one used by other tests has no fonts, so the PDF would have no text
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    subprocess.run([sys.executable, "-c", "import sys; from pathlib import Path; from voice_assistant.evaluate import make_assets; make_assets(Path(sys.argv[1]))", str(tmp_path)],
                   check=True, env=env, cwd=str(ev.CASES_FILE.parent.parent), timeout=120)
    folder = tmp_path
    for name in ("sign.png", "receipt.png", "menu_es.png", "scam_sms.png", "chart.png", "bill.pdf"):
        assert (folder / name).stat().st_size > 1000, name
    from voice_assistant.pdf_tools import read_pages

    assert "Amount due: $87.40" in read_pages(folder / "bill.pdf", "1", "bill")


def test_tools_that_change_things_are_only_recorded(tmp_path):
    from voice_assistant.attachments import Inbox
    from voice_assistant.config import Config

    calls, cfg = [], Config()
    reg = ev._registry(cfg, Inbox(), None, calls)
    assert "test run" in reg.call("launch_app", json.dumps({"name": "notepad"}))
    assert "Changed" in reg.call("change_setting", json.dumps({"key": "tts_speed", "value": 0.8}))  # on a copy that is never saved
    assert cfg.tts_speed == 1.0
    assert "Refused" in reg.call("change_setting", json.dumps({"key": "allow_shell", "value": True}))
    assert "Speaking speed" in reg.call("find_setting", json.dumps({"query": "talk slower"}))  # look-ups run for real
    assert [c[0] for c in calls] == ["launch_app", "change_setting", "change_setting", "find_setting"]


def test_spoken_numbers_count_as_digits():
    assert "87.40" in ev.spoken_numbers("You owe 87 dollars and 40 cents.")
    assert "15" in ev.spoken_numbers("a late fee of fifteen dollars")
    assert "103.80" in ev.spoken_numbers("The total is 103 dollars and 80 cents")
    assert "21" in ev.spoken_numbers("twenty-one")
