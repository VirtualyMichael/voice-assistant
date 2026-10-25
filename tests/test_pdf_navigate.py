"""Finding your way around a long PDF: outline, find-every-match search, and reading one section."""
from pypdf import PdfWriter

from voice_assistant import pdf_navigate as nav
from voice_assistant.attachments import Inbox
from voice_assistant.pdf_tools import PdfStore

PAGES = [
    "Owner's manual\nWelcome to the machine.",
    "Contents\nIntroduction ........ 3\nBrakes ........ 5\nEngine ........ 7\nElectrical ........ 9\n",
    "Introduction\nThis manual was updated on 12/03/2024 and again on 01/15/2025.",
    "Safety\nWear gloves. Part number AB-1234 is the cover.",
    "Brakes\nCheck the brake fluid every 6 months. Torque the caliper bolt to 25 Nm.",
    "Brake pads\nReplace pads at 3 mm. Part number CD-5678 fits the front. Cost $45.50.",
    "Engine\nUse 10W-40 oil. Part number AB-9999 is the filter.",
    "Engine cooling\nCoolant is a 50/50 mix.",
]


def make_pdf(tmp_path, bookmarks=True):
    w = PdfWriter()
    for _ in PAGES:
        w.add_blank_page(200, 200)
    if bookmarks:
        w.add_outline_item("Introduction", 2)
        w.add_outline_item("Safety", 3)
        brakes = w.add_outline_item("Brakes", 4)
        w.add_outline_item("Brake pads", 5, parent=brakes)
        w.add_outline_item("Engine", 6)
        w.add_outline_item("Engine cooling", 7, parent=w.add_outline_item("Appendix", 7))
    path = tmp_path / "manual.pdf"
    with open(path, "wb") as f:
        w.write(f)
    return path


def indexed(tmp_path, bookmarks=True, pages=PAGES):
    store = PdfStore(tmp_path / "cache")
    path = make_pdf(tmp_path, bookmarks)
    doc = store.get(path)
    assert doc.ready.wait(10)
    doc.pages = list(pages)  # the blank test pages have no text, so give the index the text a real manual would have
    doc.total = len(pages)
    return store, path


def test_outline_lists_the_bookmarks_with_pages(tmp_path):
    store, path = indexed(tmp_path)
    text = nav.outline(store, path, "manual.pdf")
    assert "8 pages" in text and "Brakes (PDF page 5)" in text and "  Brake pads (PDF page 6)" in text  # a child is indented under its parent


def test_outline_falls_back_to_the_printed_contents(tmp_path):
    store, path = indexed(tmp_path, bookmarks=False)
    text = nav.outline(store, path, "manual.pdf")
    assert "printed contents" in text and "Brakes (printed page 5)" in text and "Electrical (printed page 9)" in text


def test_outline_without_either_shows_the_first_line_of_each_page(tmp_path):
    plain = [f"Heading {i}\nbody text of page {i}" for i in range(1, 9)]
    store, path = indexed(tmp_path, bookmarks=False, pages=plain)
    text = nav.outline(store, path, "manual.pdf")
    assert "first line of each page" in text and "p3: Heading 3" in text


def test_exact_search_finds_every_occurrence_with_its_page(tmp_path):
    store, path = indexed(tmp_path)
    text = nav.grep(store, path, "part number", "manual.pdf")
    assert "3 match(es) on PDF pages: 4, 6, 7" in text and "AB-1234" in text and "CD-5678" in text and "AB-9999" in text


def test_regex_search_finds_dates_and_amounts(tmp_path):
    store, path = indexed(tmp_path)
    dates = nav.grep(store, path, r"\d{1,2}/\d{1,2}/\d{4}", "manual.pdf", mode="regex")
    assert "2 match(es) on PDF pages: 3 (x2)" in dates and "12/03/2024" in dates and "01/15/2025" in dates
    assert "$45.50" in nav.grep(store, path, r"\$[\d,]+\.\d\d", "manual.pdf", mode="regex")


def test_search_reports_no_match_and_bad_patterns(tmp_path):
    store, path = indexed(tmp_path)
    assert nav.grep(store, path, "hydraulic", "manual.pdf").startswith("No match")
    assert "not valid" in nav.grep(store, path, "(", "manual.pdf", mode="regex")
    assert nav.grep(store, path, "   ", "manual.pdf").startswith("Error")


def test_a_section_is_read_by_its_heading_and_stops_at_the_next_one(tmp_path):
    store, path = indexed(tmp_path)
    first, last = nav.section_range(store, path, "Safety")
    assert (first, last) == (4, 4)  # Safety is PDF page 4 and Brakes starts on page 5
    first, last = nav.section_range(store, path, "brakes")
    assert first == 5 and last <= 6  # includes its child Brake pads, then Engine starts on page 7


def test_a_missing_heading_says_so(tmp_path):
    store, path = indexed(tmp_path)
    assert "No bookmark matches" in nav.section_range(store, path, "Wiring")
    (tmp_path / "plain").mkdir()
    store2, path2 = indexed(tmp_path / "plain", bookmarks=False)
    assert "no bookmarks" in nav.section_range(store2, path2, "Brakes")


def test_the_assistant_tools_use_them(tmp_path):
    from voice_assistant.attachments import build_attachment_tools

    store, path = indexed(tmp_path)
    inbox = Inbox(tmp_path / "inbox")
    inbox.pdfs = store
    name, err = inbox.add(path)
    assert name == "manual.pdf" and not err
    tools = {t.name: t.handler for t in build_attachment_tools(inbox, lambda: None)}
    assert "Brakes (PDF page 5)" in tools["pdf_outline"]("manual.pdf")
    assert "match(es)" in tools["search_attachment"]("manual.pdf", "part number", mode="exact")
    assert "PDF page" in tools["search_attachment"]("manual.pdf", "caliper bolt torque")  # the ranked search still works
    assert "pdf_outline first" in inbox.prompt()
    assert tools["pdf_outline"]("nothing.pdf").startswith("Nothing attached is called")
