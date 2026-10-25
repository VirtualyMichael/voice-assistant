from voice_assistant.bills import amounts_in, dates_in, describe, parse_bill

ELECTRIC = """Riverside Power & Light
Account number: 4471 2290
Statement date: October 3, 2026
Service period 09/01/2026 - 09/30/2026

Amount Due: $1,284.50
Payment due date: 10/28/2026
Late fee of $15.00 applies after the due date.
"""

CARD = """FIRST NATIONAL CARD
Statement Closing Date 2026-10-10
New Balance $2,310.07
Minimum Payment Due $35.00
Payment Due Date
Nov 4, 2026
"""


def test_dates_are_read_in_the_usual_written_forms():
    assert dates_in("due 10/28/2026") == [("2026-10-28", "10/28/2026")]
    assert dates_in("by 28 October 2026 please")[0][0] == "2026-10-28"
    assert dates_in("Nov 4, 2026")[0][0] == "2026-11-04"
    assert dates_in("2026-12-01")[0][0] == "2026-12-01"
    assert dates_in("3/9/27")[0][0] == "2027-03-09"
    assert dates_in("13/45/2026") == [] and dates_in("no dates here") == []


def test_amounts_keep_cents_and_drop_thousands_separators():
    assert amounts_in("Total $1,284.50 and $15.00 fee") == ["1284.50", "15.00"]
    assert amounts_in("about 5 dollars") == []


def test_an_electric_bill():
    b = parse_bill(ELECTRIC)
    assert (b.due, b.amount) == ("2026-10-28", "1284.50")
    assert b.payee == "Riverside Power & Light"
    assert "2026-10-03" not in (b.due,)  # the statement date is not the due date


def test_a_card_statement_with_the_date_on_the_next_line():
    b = parse_bill(CARD)
    assert b.due == "2026-11-04"
    assert b.amount == "2310.07" and b.minimum == "35.00"


def test_the_report_quotes_the_document_and_suggests_the_calendar_call():
    text = describe(parse_bill(ELECTRIC), "power.pdf")
    assert "2026-10-28" in text and "$1284.50" in text and "phone_add_calendar_event" in text and "Payment due date" in text
    assert "Could not find" in describe(parse_bill("Hello, nothing here."), "x.txt")


def test_an_attached_text_bill_is_read_through_the_tool(tmp_path):
    from voice_assistant.attachments import Inbox

    inbox = Inbox(tmp_path / "inbox")
    f = tmp_path / "water.txt"
    f.write_text(ELECTRIC, encoding="utf-8")
    name, _ = inbox.add(f)
    out = inbox.bill(name, get_llm=None)
    assert "2026-10-28" in out and "1284.50" in out and "Information only" in out
    assert "Nothing attached" in inbox.bill("missing", None)
