"""Pull the due date and the amount out of a bill (a PDF, a text file, or a photo or screenshot of one).

The numbers are found by plain pattern matching, not by the language model, so a small model cannot invent a date or an amount: every value returned
was read from the document, together with the line it came from. The assistant then offers a calendar event (the phone shows it and the user taps Yes).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE_RES = [
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b"), "mdy"),
    (re.compile(rf"\b{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I), "monthname"),
    (re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s+(\d{{4}})\b", re.I), "daymonth"),
]
_AMOUNT = re.compile(r"(?<![\w.])(?:US)?\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)\b")
DUE_WORDS = ("due date", "payment due", "pay by", "due on", "due by", "please pay by", "pay on or before", "due:")
AMOUNT_FIRST = ("amount due", "total amount due", "total due", "balance due", "please pay", "pay this amount", "new balance", "amount to pay", "total to pay")
AMOUNT_LATER = ("minimum payment", "minimum amount due", "min payment", "minimum due")


@dataclass
class Bill:
    due: str = ""  # YYYY-MM-DD
    due_line: str = ""
    amount: str = ""  # "123.45"
    amount_line: str = ""
    minimum: str = ""
    payee: str = ""
    others: list[str] = field(default_factory=list)  # other dates that were found, as written


def _iso(kind: str, g: tuple, today: date | None = None) -> str | None:
    try:
        if kind == "ymd":
            y, m, d = int(g[0]), int(g[1]), int(g[2])
        elif kind == "mdy":
            m, d, y = int(g[0]), int(g[1]), int(g[2])
            if y < 100:
                y += 2000
        elif kind == "monthname":
            m, d, y = MONTHS[g[0][:3].lower()], int(g[1]), int(g[2])
        else:
            d, m, y = int(g[0]), MONTHS[g[1][:3].lower()], int(g[2])
        return date(y, m, d).isoformat()
    except (ValueError, KeyError):
        return None


def dates_in(line: str) -> list[tuple[str, str]]:
    """[(ISO date, the text as written)] for every date in the line, in order of appearance."""
    found = []
    for rx, kind in _DATE_RES:
        for m in rx.finditer(line):
            iso = _iso(kind, m.groups())
            if iso:
                found.append((m.start(), iso, m.group(0)))
    found.sort()
    seen, out = set(), []
    for _pos, iso, raw in found:
        if iso not in seen:
            seen.add(iso)
            out.append((iso, raw))
    return out


def amounts_in(line: str) -> list[str]:
    return [m.group(1).replace(",", "") for m in _AMOUNT.finditer(line)]


def parse_bill(text: str) -> Bill:
    """Find the due date and amount. A label and its value are often on neighbouring lines, so each label also looks at the next line."""
    lines = [" ".join(raw.split()) for raw in text.splitlines()]
    lines = [ln for ln in lines if ln]
    bill = Bill()
    for i, line in enumerate(lines):
        low = line.lower()
        window = line + " " + (lines[i + 1] if i + 1 < len(lines) else "")
        if not bill.amount and any(w in low for w in AMOUNT_FIRST):
            amts = amounts_in(line) or amounts_in(window)
            if amts:
                bill.amount, bill.amount_line = amts[0], line[:160]
        if not bill.minimum and any(w in low for w in AMOUNT_LATER):
            amts = amounts_in(line) or amounts_in(window)
            if amts:
                bill.minimum = amts[0]
    # the due date: a label with the date on the same line wins over a label whose date is on the next line
    for with_next in (False, True):
        for i, line in enumerate(lines):
            if bill.due:
                break
            if any(w in line.lower() for w in DUE_WORDS):
                ds = dates_in(line) or (dates_in(lines[i + 1]) if with_next and i + 1 < len(lines) else [])
                if ds:
                    bill.due, bill.due_line = ds[0][0], line[:160]
    seen = {bill.due}
    for line in lines:
        for iso, raw in dates_in(line):
            if iso not in seen and len(bill.others) < 4:
                seen.add(iso)
                bill.others.append(raw)
    for line in lines[:12]:  # the company is usually named at the top; skip lines that are mostly numbers
        letters = sum(c.isalpha() for c in line)
        if 3 <= len(line) <= 60 and letters >= len(line) * 0.6 and not any(w in line.lower() for w in ("statement", "invoice", "page", "account", "bill")):
            bill.payee = line
            break
    return bill


def describe(bill: Bill, name: str) -> str:
    """What the assistant is told. Everything here was read from the document; nothing is guessed."""
    if not bill.due and not bill.amount and not bill.minimum:
        return (f"Could not find a due date or an amount in '{name}'. It may be a scanned picture without readable text: try look_at_attachment and read the due date and amount from "
                "what it shows, or ask the user.")
    rows = [f"Read from '{name}' (the document's own words, not guesses):"]
    if bill.payee:
        rows.append(f"- From: {bill.payee}")
    if bill.due:
        rows.append(f"- Due date: {bill.due}   (line: “{bill.due_line}”)")
    else:
        rows.append("- Due date: not found" + (f"; other dates in it: {', '.join(bill.others)}" if bill.others else ""))
    if bill.amount:
        rows.append(f"- Amount due: ${bill.amount}   (line: “{bill.amount_line}”)")
    if bill.minimum:
        rows.append(f"- Minimum payment: ${bill.minimum}")
    if bill.due:
        title = f"Pay {bill.payee or 'bill'}" + (f" ${bill.amount}" if bill.amount else "")
        rows.append(f"To put it in the calendar, call phone_add_calendar_event with title “{title}”, start {bill.due}T09:00 (all_day true). The user taps Yes on their phone. "
                    "Tell them the date and amount you read, and that they should check them against the bill.")
    return "\n".join(rows)
