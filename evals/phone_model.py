"""Measure an AI model running on the phone itself (see the phone app's OnDeviceModel.kt), for deciding whether it can answer when the PC is off.

    .venv\\Scripts\\python evals\\phone_model.py [--cpu] [--only knowledge,commands]

Needs a debug build of the phone app over adb, with a .litertlm model file in the app's files/models/ folder:
    adb push gemma-4-E2B-it-gpu.litertlm /sdcard/Android/data/com.voiceassistant.phone/files/models/
Two kinds of question: everyday ones from the test set that need no tools (maths, dates, knowledge, scams), and offline commands
("wake me at 6:30") that a fallback would have to turn into a phone action by itself. Answers are scored with the test set's own checker.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voice_assistant.evaluate import CASES_FILE, check  # noqa: E402

ADB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe")
APP = "com.voiceassistant.phone/.MainActivity"

COMMANDS_SYSTEM = (  # keep in step with OfflineHelper.system in the phone app
    "You are the offline helper of a voice assistant on a phone; the user's PC is out of reach. Today is Saturday 24 October 2026, 14:00. "
    "Turn the user's request into exactly one JSON object and nothing else (no code fences). The actions are:\n"
    '{"action":"set_alarm","time":"HH:MM"} with a 24-hour time, e.g. 7:15 in the evening is "19:15".\n'
    '{"action":"set_timer","seconds":N} where N is one plain number of seconds, e.g. 10 minutes is 600 and an hour and a half is 5400.\n'
    '{"action":"send_text","to":"NAME","message":"TEXT"} and {"action":"call","to":"NAME"}.\n'
    '{"action":"answer","text":"SHORT ANSWER"} for maths, dates, conversions, and general knowledge you are sure of; work it out carefully and keep it to one or two sentences.\n'
    '{"action":"unavailable","reason":"SHORT REASON"} for anything that needs the internet, the PC or other devices (weather, news, lights, music, searching, documents, photos), or that you are not sure about.'
)
COMMANDS = [
    ("cmd_alarm", "Wake me up at 6:30 tomorrow morning", {"action": "set_alarm", "time": r"^0?6:30$"}),
    ("cmd_alarm_pm", "Set an alarm for quarter past seven in the evening", {"action": "set_alarm", "time": r"^19:15$"}),
    ("cmd_timer", "Start a 10 minute timer for the pasta", {"action": "set_timer", "seconds": r"^600$"}),
    ("cmd_timer_mixed", "Timer for an hour and a half", {"action": "set_timer", "seconds": r"^5400$"}),
    ("cmd_text", "Text Mom that I'm running ten minutes late", {"action": "send_text", "to": r"(?i)^mom$", "message": r"(?i)late"}),
    ("cmd_call", "Call Dad", {"action": "call", "to": r"(?i)^dad$"}),
    ("cmd_answer_math", "What's 15 percent of 80?", {"action": "answer", "text": r"\b12\b"}),
    ("cmd_lights", "Turn on the living room lights", {"action": "unavailable"}),
    ("cmd_weather", "What's the weather tomorrow?", {"action": "unavailable"}),
    ("cmd_music", "Play some jazz", {"action": "unavailable"}),
]


def adb(*args, timeout=30) -> str:
    return subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace").stdout


def ask(prompt: str, system: str = "", cpu: bool = False, timeout_s: float = 240) -> tuple[bool, float, str]:
    """(worked, seconds, answer) from the phone."""
    rid = f"q{int(time.time() * 1000) % 10_000_000}"
    b64 = lambda s: base64.b64encode(s.encode()).decode()  # noqa: E731
    # -S restarts the app so the question arrives with the launch; a second `am start` on a running app is silently dropped
    args = ["shell", "am", "start", "-S", "-n", APP, "--es", "eval_id", rid, "--es", "eval_prompt_b64", b64(prompt)]
    if system:
        args += ["--es", "eval_system_b64", b64(system)]
    if cpu:
        args += ["--ez", "eval_cpu", "true"]
    adb(*args)
    end = time.time() + timeout_s
    while time.time() < end:
        out = adb("logcat", "-d", "-s", "VA_EVAL:I")
        m = re.search(rf"VA_EVAL\s*:\s*{rid} (ok|error) (\d+) (\S*)", out)
        if m:
            return m.group(1) == "ok", int(m.group(2)) / 1000, base64.b64decode(m.group(3)).decode("utf-8", "replace")
        time.sleep(1)
    return False, timeout_s, "no answer in time"


def knowledge_cases() -> list[dict]:
    """Test-set questions a model can answer with no tools, no attachments and no web."""
    out = []
    for c in json.loads(CASES_FILE.read_text(encoding="utf-8")):
        if c.get("attach") or c.get("tools") or c.get("not_tools") or not c.get("expect") or c["id"] in ("research_honest_unknown",):
            continue
        if c["category"] in ("basics", "documents", "research"):
            out.append(c)
    return out


def score_command(raw: str, want: dict) -> list[str]:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return ["no JSON in the answer"]
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return ["the JSON does not parse"]
    problems = []
    for k, v in want.items():
        val = str(got.get(k, ""))
        if k == "action":
            if val != v:
                problems.append(f"action {val!r}, expected {v!r}")
        elif not re.search(v, val):
            problems.append(f"{k}={val!r} does not match {v}")
    return problems


def main(argv: list[str]) -> int:
    cpu = "--cpu" in argv
    only = argv[argv.index("--only") + 1].split(",") if "--only" in argv else ["knowledge", "commands"]
    adb("logcat", "-c")
    status = adb("shell", "am", "start", "-S", "-n", APP, "--es", "eval_id", "status", "--ez", "eval_status", "true")
    time.sleep(3)
    m = re.search(r"status ok \d+ (\S+)", adb("logcat", "-d", "-s", "VA_EVAL:I"))
    print("Phone model:", base64.b64decode(m.group(1)).decode() if m else f"(no status) {status.strip()}")
    rows = []
    if "knowledge" in only:
        for c in knowledge_cases():
            ok, secs, answer = ask(c["question"], "You are a helpful voice assistant. Answer in one to three short sentences.", cpu)
            problems = check(c, answer, []) if ok else [answer]
            rows.append(("knowledge", c["id"], not problems, secs, answer, problems))
            print(f"  {'PASS' if not problems else 'FAIL'}  knowledge {c['id']:30} {secs:6.1f}s  {'; '.join(problems)[:120]}")
    if "commands" in only:
        for cid, prompt, want in COMMANDS:
            ok, secs, answer = ask(prompt, COMMANDS_SYSTEM, cpu)
            problems = score_command(answer, want) if ok else [answer]
            rows.append(("commands", cid, not problems, secs, answer, problems))
            print(f"  {'PASS' if not problems else 'FAIL'}  commands  {cid:30} {secs:6.1f}s  {'; '.join(problems)[:120]}")
    for kind in ("knowledge", "commands"):
        sub = [r for r in rows if r[0] == kind]
        if sub:
            times = sorted(r[3] for r in sub[1:]) or [sub[0][3]]  # the first answer includes loading the model
            print(f"{kind}: {sum(r[2] for r in sub)}/{len(sub)} passed, typical answer {times[len(times) // 2]:.1f}s (first, with loading: {sub[0][3]:.1f}s)")
    from voice_assistant.config import config_dir

    out = config_dir() / "evals" / f"phone-model-{time.strftime('%Y%m%d-%H%M%S')}.json"  # beside the test set's own runs
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([{"kind": k, "id": i, "ok": ok, "seconds": s, "answer": a, "problems": p} for k, i, ok, s, a, p in rows], indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
