import base64
import json
import threading

from test_remote import Setup, collect, has
from voice_assistant.answer_cards import CardCollector, shrink_jpeg, site_name
from voice_assistant.tools import Tool, ToolRegistry

SEARCH = ("[Untrusted web content.]\n1. Sydney Opera House - Wikipedia - https://en.wikipedia.org/wiki/Sydney_Opera_House\n   It opened in 1973.\n"
          "2. Visit the Opera House - https://www.sydneyoperahouse.com/visit\n   Tours daily.\n")


def turn(c: CardCollector, *calls, answer=""):
    c.user_final("question")
    for name, args, result in calls:
        c.tool(name, args, result)
    c.assistant_delta(answer)
    return c.card()


def test_search_results_become_sources_with_pages_it_read_first():
    card = turn(CardCollector(), ("web_search", {"query": "opera house"}, SEARCH),
                ("fetch_page", {"url": "https://www.sydneyoperahouse.com/visit"}, "[Untrusted]..."), answer="It opened in 1973.")
    assert [s["site"] for s in card["sources"]] == ["sydneyoperahouse.com", "en.wikipedia.org"]
    assert card["sources"][1]["title"] == "Sydney Opera House - Wikipedia"


def test_links_in_the_reply_are_sources_too_and_a_new_turn_starts_empty():
    c = CardCollector()
    card = turn(c, answer="See https://python.org/downloads/. Done.")
    assert card["sources"][0]["url"] == "https://python.org/downloads/"
    assert turn(c, answer="Just a plain answer.") is None


def test_a_failed_page_is_not_a_source_and_settings_link_to_their_section():
    card = turn(CardCollector(), ("fetch_page", {"url": "https://x.example/"}, "Error: HTTP 404"), ("find_setting", {"query": "talk slower"}, "..."))
    assert card["sources"] == [] and card["settings"][0] == {"section": "speech", "label": "Voice › Speaking speed"}


def test_pictures_are_downloaded_by_the_pc_and_failures_are_dropped():
    found = "intro\n1 | A capybara | https://img.example/1.jpg | https://page.example/a\n2 | Another | https://img.example/2.jpg | https://page.example/b"
    fetched = []
    c = CardCollector(lambda url: fetched.append(url) or (b"\xff\xd8jpeg" if url.endswith("1.jpg") else None))
    card = turn(c, ("find_images", {"query": "capybara"}, found))
    pics = c.pictures(card.pop("_pending"))
    assert sorted(fetched) == ["https://img.example/1.jpg", "https://img.example/2.jpg"]
    assert len(pics) == 1 and base64.b64decode(pics[0]["jpeg_b64"]) == b"\xff\xd8jpeg" and pics[0]["page"] == "https://page.example/a"


def test_pictures_are_off_without_the_internet():
    c = CardCollector(None)
    card = turn(c, ("find_images", {}, "1 | A | https://i/1.jpg | https://p/1"))
    assert c.pictures(card["_pending"]) == []


def test_the_registry_tells_its_listeners_about_every_call():
    seen = []
    reg = ToolRegistry([Tool("echo", "", {}, lambda text="": text.upper())])
    reg.listeners.append(lambda *a: seen.append(a))
    reg.listeners.append(lambda *a: 1 / 0)  # a broken listener changes nothing
    assert reg.call("echo", json.dumps({"text": "hi"})) == "HI"
    assert seen == [("echo", {"text": "hi"}, "HI")] and reg.copy().listeners is reg.listeners


def test_shrink_makes_a_small_jpeg():
    from PySide6.QtGui import QColor, QImage

    img = QImage(2000, 1000, QImage.Format_RGB32)
    img.fill(QColor("#336699"))
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    raw = QByteArray()
    buf = QBuffer(raw)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    out = shrink_jpeg(bytes(raw))
    back = QImage.fromData(out)
    assert out.startswith(b"\xff\xd8") and max(back.width(), back.height()) == 480
    assert shrink_jpeg(b"not a picture") is None and site_name("https://www.bbc.co.uk/x") == "bbc.co.uk"


def test_the_phone_gets_the_card_after_the_reply(tmp_path):
    s = Setup(tmp_path)
    try:
        from voice_assistant.session import SessionEvents

        c = CardCollector(lambda url: b"\xff\xd8jpeg")

        class ModelSearches(SessionEvents):  # as if the model searched during this turn (after the collector starts it)
            def user_final(self, text):
                c.tool("web_search", {}, SEARCH)
                c.tool("find_images", {}, "1 | Pic | https://img.example/1.jpg | https://page.example/a")

        s.multi.add(c)
        s.multi.add(ModelSearches())
        s.server.cards = c
        ws = s.connect()
        collect(ws, has("ready"))
        s.session.submit_text("when did it open?")
        messages = collect(ws, lambda m: sum(1 for x in m if isinstance(x, dict) and x["type"] == "card") >= 2, timeout=6)
        cards = [m for m in messages if isinstance(m, dict) and m["type"] == "card"]
        kinds = [m["type"] for m in messages if isinstance(m, dict)]
        assert kinds.index("done") < kinds.index("card")
        assert cards[0]["id"] == cards[1]["id"] and cards[1]["images"][0]["page"] == "https://page.example/a"
    finally:
        s.close()
