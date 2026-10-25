"""The quality test set: real questions, pictures and documents with what a good answer must contain, run against the model you use.

    python -m voice_assistant eval [--only vision,phone] [--case ID] [--verbose]

Each case is asked in a fresh conversation with the app's own tools. Tools that only look things up run for real (the time, web search when
"Use the internet" is on, reading attachments, finding settings); tools that would change something (open apps, set alarms, change settings,
send anything) are recorded instead, and the case checks that the right one was called with the right arguments. Results are saved under
the settings folder in evals/, and each run is compared with the previous one, so a change that makes the assistant worse shows up at once.
The pictures and PDFs are drawn by this file the first time, so the test set needs no downloads.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import config_dir, load_config

CASES_FILE = Path(__file__).resolve().parent.parent / "evals" / "cases.json"
READ_ONLY = {"get_time", "system_status", "web_search", "fetch_page", "find_setting", "read_attachment", "search_attachment", "pdf_outline", "read_section", "deep_research", "look_at_attachment",
             "list_timers", "list_apps", "list_routines", "find_images", "research",
             # these run for real too: here they only reach a pretend phone and a settings copy that is never saved
             "change_setting", "phone_set_alarm", "phone_set_timer", "phone_add_calendar_event"}


@dataclass
class Result:
    id: str
    category: str
    ok: bool
    seconds: float
    answer: str
    calls: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    skipped: str = ""


# ---- the pictures and documents the cases look at ----------------------------------------------------------
def make_assets(folder: Path) -> Path:
    """Draw the test pictures and PDFs with Qt (already part of the app) unless they exist."""
    folder.mkdir(parents=True, exist_ok=True)
    wanted = {"sign.png", "receipt.png", "menu_es.png", "scam_sms.png", "chart.png", "bill.pdf"}
    if all((folder / n).exists() for n in wanted):
        return folder
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPageSize, QPainter, QPdfWriter

    _app = QGuiApplication.instance() or QGuiApplication(["eval"])  # noqa: F841 - fonts need an application

    def picture(name, w, h, bg, draw):
        img = QImage(w, h, QImage.Format_RGB32)
        img.fill(QColor(bg))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        draw(p)
        p.end()
        img.save(str(folder / name))

    def text(p, x, y, s, size=28, bold=False, color="#111111"):
        f = QFont("Arial", size)
        f.setBold(bold)
        p.setFont(f)
        p.setPen(QColor(color))
        p.drawText(x, y, s)

    def sign(p):
        p.fillRect(40, 40, 520, 520, QColor("#c62828"))
        text(p, 120, 220, "NO PARKING", 52, True, "#ffffff")
        text(p, 130, 320, "8 AM - 6 PM", 44, True, "#ffffff")
        text(p, 150, 400, "MON - FRI", 40, True, "#ffffff")
        text(p, 110, 480, "Tow-away zone", 30, False, "#ffffff")

    def receipt(p):
        lines = [("CORNER MARKET", True), ("12 Elm Street", False), ("", False), ("Milk 2L            3.49", False), ("Bread              2.99", False),
                 ("Coffee beans      12.50", False), ("Bananas            1.89", False), ("", False), ("SUBTOTAL          20.87", False),
                 ("TAX                2.60", False), ("TOTAL             23.47", True), ("", False), ("VISA ****4417", False), ("2026-03-14 18:22", False)]
        for i, (s, b) in enumerate(lines):
            f = QFont("Courier New", 22)
            f.setBold(b)
            p.setFont(f)
            p.setPen(QColor("#222222"))
            p.drawText(40, 60 + i * 38, s)

    def menu(p):
        text(p, 40, 70, "MENÚ DEL DÍA", 34, True)
        for i, s in enumerate(["Sopa de tomate ........ 4,50 €", "Pollo asado con patatas ........ 11,00 €", "Merluza a la plancha ........ 13,50 €",
                               "Tarta de queso ........ 5,00 €", "Agua o vino incluido"]):
            text(p, 40, 140 + i * 56, s, 24)

    def sms(p):
        p.fillRect(30, 60, 640, 300, QColor("#e9e9eb"))
        text(p, 50, 40, "+1 (838) 555-0142", 20, True, "#555555")
        for i, s in enumerate(["USPS: Your package is on hold due to an", "incomplete address. Update within 12 hours", "or it will be returned:",
                               "http://usps-redelivery-help.top/track", "Reply STOP to opt out"]):
            text(p, 50, 110 + i * 46, s, 22)

    def chart(p):
        text(p, 40, 50, "Monthly visitors (thousands)", 24, True)
        for i, (m, v) in enumerate([("Jan", 12), ("Feb", 18), ("Mar", 31), ("Apr", 24)]):
            x = 80 + i * 140
            p.fillRect(x, 420 - v * 10, 90, v * 10, QColor("#3f6fd8"))
            text(p, x + 20, 455, m, 20)
            text(p, x + 25, 410 - v * 10, str(v), 20, True)

    picture("sign.png", 600, 600, "#ffffff", sign)
    picture("receipt.png", 560, 620, "#fbfbf7", receipt)
    picture("menu_es.png", 760, 460, "#fff8e8", menu)
    picture("scam_sms.png", 700, 400, "#ffffff", sms)
    picture("chart.png", 680, 480, "#ffffff", chart)

    pdf = QPdfWriter(str(folder / "bill.pdf"))
    pdf.setPageSize(QPageSize(QPageSize.A4))
    p = QPainter(pdf)
    scale = pdf.resolution() / 72
    f = QFont("Arial", 14)
    p.setFont(f)
    for i, s in enumerate(["Riverside Water Company", "Account 55-20931", "", "Statement date: 3 March 2026", "Amount due: $87.40",
                           "Payment due by: 28 March 2026", "", "Late fee after the due date: $15.00", "Pay online at riversidewater.example or call 555-0199."]):
        p.drawText(QRectF(60 * scale, (80 + i * 26) * scale, 480 * scale, 24 * scale), Qt.AlignLeft, s)
    p.end()
    return folder


# ---- checking an answer -------------------------------------------------------------------------------
_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def spoken_numbers(text: str) -> str:
    """The answer as a voice assistant says it, with the numbers written as digits too: "87 dollars and 40 cents" also reads "87.40",
    "fifteen dollars" also reads "15", so a check for "87.40" passes either way."""
    def words(m):
        a = _TENS.get(m.group(1), 0) if m.group(1) in _TENS else _UNITS.index(m.group(1))
        return str(a + (_UNITS.index(m.group(2)) if m.group(2) else 0))

    t = text.lower()
    t = re.sub(r"\b(" + "|".join(list(_TENS) + _UNITS) + r")(?:[- ](" + "|".join(_UNITS[1:10]) + r"))?\b", words, t)
    t = re.sub(r"(\d+) dollars? and (\d{1,2}) cents?", lambda m: f"{m.group(1)}.{int(m.group(2)):02d}", t)
    return text.lower() + " " + t


def check(case: dict, answer: str, calls: list) -> list[str]:
    """What is wrong with this answer (empty: it passed)."""
    problems, text = [], spoken_numbers(answer)
    expect = case.get("expect", {})
    for word in expect.get("all", []):
        if word.lower() not in text:
            problems.append(f"the answer should mention '{word}'")
    for key in ("any", "any2"):
        anyof = expect.get(key, [])
        if anyof and not any(w.lower() in text for w in anyof):
            problems.append("the answer should mention one of: " + ", ".join(anyof))
    pages = case.get("read_pages", 0)
    read = sum(1 for c in calls if c[0] == "fetch_page" and not str(c[2] if len(c) > 2 else "").startswith("Error")) + sum(
        len(re.findall(r"^\[\d+\] ", str(c[2]), re.M)) for c in calls if c[0] == "research" and len(c) > 2)
    if pages and read < pages:
        problems.append(f"it should have read at least {pages} page(s), not only search snippets")
    for word in expect.get("none", []):
        if word.lower() in text:
            problems.append(f"the answer should not say '{word}'")
    for want in case.get("tools", []):
        name, args = want["name"], want.get("args", {})
        found = [c for c in calls if re.fullmatch(name, c[0])]  # "web_search|research": either tool will do
        if not found:
            problems.append(f"it should have called {name}")
            continue
        if args and not any(all(re.search(str(pat), str(c[1].get(k, "")), re.I) for k, pat in args.items()) for c in found):
            problems.append(f"{name} was called with {found[-1][1]}, expected {args}")
    for name in case.get("not_tools", []):
        bad = [c for c in calls if c[0] == name and all(re.search(str(p), str(c[1].get(k, "")), re.I) for k, p in case.get("not_tools_args", {}).get(name, {}).items())]
        if bad:
            problems.append(f"it should not have called {name} ({bad[0][1]})")
    return problems


# ---- running ------------------------------------------------------------------------------------------
def _registry(cfg, inbox, llm, record):
    """The app's tools, with everything that changes something recorded instead of done."""
    from .agent_tools import build_agent_tools
    from .attachments import build_attachment_tools
    from .permissions import FolderPolicy
    from .phone_actions import build_phone_action_tools
    from .remote_settings import SettingsService
    from .settings_tools import build_settings_tools
    from .tools import ToolRegistry

    def no(_prompt):
        return False

    class Phone:  # the phone says yes, so the case sees what the model asked for
        def request_action(self, action, args):
            return True, "Done on the phone."

    tools = build_agent_tools(cfg, FolderPolicy([]), no, lambda _t: None)
    if cfg.allow_web:
        from .answer_cards import build_image_tools
        from .research import build_research_tools

        tools += build_image_tools() + build_research_tools()
    tools += build_attachment_tools(inbox, lambda: llm, lambda: cfg.main_language)
    from dataclasses import replace

    tools += build_settings_tools(SettingsService(replace(cfg), lambda: False, lambda _k: [], lambda _c: None), lambda _q: True)  # a copy: never saved
    tools += build_phone_action_tools(lambda: Phone())
    for t in tools:
        real = t.handler

        def handler(_real=real, _name=t.name, **kw):
            entry = [_name, kw, ""]
            record.append(entry)
            entry[2] = str(_real(**kw)) if _name in READ_ONLY else "Done (test run: nothing was really done)."
            return entry[2]

        t.handler = handler
    return ToolRegistry(tools)


def run_case(case: dict, cfg, llm, assets: Path, timeout_s: float = 180) -> Result:
    from .agent import Agent
    from .agent_tools import capabilities_prompt
    from .attachments import Inbox
    from .pdf_tools import PdfStore
    from .phone_actions import PHONE_ACTIONS_PROMPT
    from .settings_tools import SETTINGS_PROMPT

    needs = set(case.get("needs", []))
    if "web" in needs and not cfg.allow_web:
        return Result(case["id"], case["category"], False, 0, "", skipped="needs 'Use the internet' turned on")
    inbox = Inbox()
    inbox.pdfs = PdfStore()
    for name in case.get("attach", []):
        inbox.add(assets / name)
    calls: list = []
    registry = _registry(cfg, inbox, llm, calls)
    from .research import RESEARCH_PROMPT

    prompt = (cfg.system_prompt + capabilities_prompt(cfg, registry.names()) + SETTINGS_PROMPT + PHONE_ACTIONS_PROMPT
              + (RESEARCH_PROMPT if "research" in registry.names() else "") + inbox.prompt())
    agent = Agent(llm, prompt, registry, max(cfg.max_tool_rounds, 6), max_messages=40)
    cancel, parts, started = threading.Event(), [], time.monotonic()
    worker = threading.Thread(target=lambda: parts.extend(agent.respond(case["question"], cancel)), daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        cancel.set()
        return Result(case["id"], case["category"], False, timeout_s, "".join(parts), calls, [f"no answer within {timeout_s:.0f} s"])
    answer = "".join(parts).strip()
    problems = check(case, answer, calls)
    return Result(case["id"], case["category"], not problems, time.monotonic() - started, answer, calls, problems)


def main(argv: list[str]) -> int:
    from .providers import make_llm

    only = next((a.split("=", 1)[1] for a in argv if a.startswith("--only=")), "")
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    one = argv[argv.index("--case") + 1] if "--case" in argv else ""
    verbose = "--verbose" in argv
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    cases = [c for c in cases if (not only or c["category"] in only.split(",")) and (not one or c["id"] == one)]
    cfg = load_config()
    llm = make_llm(cfg)
    assets = make_assets(config_dir() / "evals" / "assets")
    print(f"Testing {len(cases)} cases against {cfg.llm_provider} / {cfg.llm_model}" + ("" if cfg.allow_web else " (web cases skipped: 'Use the internet' is off)"))
    results = []
    for c in cases:
        r = run_case(c, cfg, llm, assets)
        results.append(r)
        mark = "skip" if r.skipped else ("PASS" if r.ok else "FAIL")
        print(f"  {mark:4}  {r.category:9} {r.id:28} {r.seconds:5.1f}s  {r.skipped or '; '.join(r.problems)}")
        if verbose or (not r.ok and not r.skipped):
            print("        answer: " + r.answer.replace("\n", " ")[:300])
            if r.calls:
                print("        tools:  " + "; ".join(f"{c[0]}({json.dumps(c[1])[:80]})" for c in r.calls))
    ran = [r for r in results if not r.skipped]
    passed = sum(r.ok for r in ran)
    by_cat: dict[str, list] = {}
    for r in ran:
        by_cat.setdefault(r.category, []).append(r.ok)
    print(f"\n{passed}/{len(ran)} passed" + (f"  ({len(results) - len(ran)} skipped)" if len(results) > len(ran) else ""))
    print("  " + "   ".join(f"{k} {sum(v)}/{len(v)}" for k, v in sorted(by_cat.items())))
    out = config_dir() / "evals"
    previous = sorted(out.glob("run-*.json"))
    record = {"model": f"{cfg.llm_provider}/{cfg.llm_model}", "at": time.strftime("%Y-%m-%d %H:%M"), "passed": passed, "ran": len(ran),
              "results": [{"id": r.id, "ok": r.ok, "seconds": round(r.seconds, 1), "problems": r.problems, "answer": r.answer[:2000], "skipped": r.skipped} for r in results]}
    if previous and not one:
        old = {x["id"]: x["ok"] for x in json.loads(previous[-1].read_text(encoding="utf-8"))["results"] if not x.get("skipped")}
        worse = [r.id for r in ran if old.get(r.id) and not r.ok]
        better = [r.id for r in ran if old.get(r.id) is False and r.ok]
        if worse or better:
            print(f"  compared with {previous[-1].stem}: newly failing {worse or 'none'}, newly passing {better or 'none'}")
    if not one:
        (out / f"run-{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0 if passed == len(ran) else 1
