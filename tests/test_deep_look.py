import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import httpx
import pytest

from voice_assistant import deep_look as D
from voice_assistant.config import Config
from voice_assistant.llm import StreamEvent

PNG = None


def tiny_photo() -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QGuiApplication, QImage

    QGuiApplication.instance() or QGuiApplication([])
    img = QImage(400, 300, QImage.Format_RGB32)
    img.fill(QColor("green"))
    out = QByteArray()
    buf = QBuffer(out)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(out)


class FakeLLM:
    """Answers each kind of request from a script, and records what it was asked."""

    def __init__(self, observe, verify=None, inspect="A tiny label reading 'Monstera'."):
        self.observe, self.verify, self.inspect, self.calls = observe, verify, inspect, []

    def stream_chat(self, messages):
        system = messages[0]["content"]
        images = [p for p in messages[1]["content"] if p["type"] == "image_url"]
        self.calls.append((system[:30], len(images)))
        if system.startswith("You are a careful visual analyst"):
            text = json.dumps(self.observe)
        elif system.startswith("You compare"):
            text = json.dumps(self.verify or {"match": 0.9, "shared_features": ["split leaves"], "differences": []})
        else:
            text = self.inspect
        yield StreamEvent("text", text)


PLANT = {
    "answer": "A green houseplant with large split leaves.", "description": "Large glossy leaves with holes.", "category": "plant",
    "text_seen": [], "candidates": [{"name": "Monstera deliciosa", "confidence": 0.7, "why": "split, perforated leaves"}],
    "queries": ["large glossy split leaf houseplant with holes"], "regions": [{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4, "look_for": "the leaf edge"}],
    "needs_research": True,
}


class FakeWeb(D.Web):
    def __init__(self, known=("Monstera deliciosa",)):
        self.known, self.searched, self.ref_bytes = known, [], tiny_photo()
        self._search = None

    def search(self, query, n=5):
        self.searched.append(query)
        return [{"title": "Monstera care", "href": "https://example.org/monstera", "body": "Toxic to cats and dogs if eaten."}]

    def wikipedia(self, name):
        return {"title": name, "extract": f"{name} is a species of flowering plant.", "url": "https://en.wikipedia.org/wiki/Monstera_deliciosa", "image": ""}

    def taxon(self, name):
        if name not in self.known:
            return None
        return {"name": name, "common": "Swiss cheese plant", "rank": "species", "group": "Plantae", "wikipedia": "", "url": "https://www.inaturalist.org/taxa/1",
                "image": "https://static.inaturalist.org/photos/1/medium.jpg"}

    def reference_image(self, url):
        return self.ref_bytes

    def plantnet(self, key, data, mime):
        return [{"name": "Monstera deliciosa", "common": "Swiss cheese plant", "score": 0.93, "family": "Araceae"}]


def look(cfg=None, llm=None, web=None, **kw):
    cfg = cfg or Config(allow_web=True, deep_vision=True)
    notes = []
    d = D.DeepLook(cfg, lambda: llm, web or FakeWeb(), notify=notes.append, **kw)
    return d, notes


def test_a_plant_photo_is_inspected_researched_and_verified():
    llm = FakeLLM(PLANT)
    web = FakeWeb()
    d, notes = look(llm=llm, web=web)
    out = d.run(tiny_photo(), "image/png", "what plant is this?")
    assert "Best match: Monstera deliciosa" in out and "species Monstera deliciosa (Swiss cheese plant)" in out
    assert "visual match" in out and "Confidence high" in out
    assert "Toxic to cats and dogs" in out and "https://en.wikipedia.org/wiki/Monstera_deliciosa" in out
    assert "Close-up check - the leaf edge" in out and "Searched for: large glossy split leaf houseplant with holes" in out
    assert web.searched == ["large glossy split leaf houseplant with holes"] and notes and "Looking closer" in notes[0]
    assert any(n == 2 for _, n in llm.calls)  # the verify call looked at two pictures


def test_it_is_off_without_web_access_or_when_switched_off():
    for cfg in (Config(allow_web=False, deep_vision=True), Config(allow_web=True, deep_vision=False)):
        d, _ = look(cfg=cfg, llm=FakeLLM(PLANT))
        assert d.run(tiny_photo(), "image/png", "what is this") is None


def test_simple_questions_skip_the_web():
    simple = {**PLANT, "needs_research": False, "category": "other", "queries": [], "candidates": []}
    web = FakeWeb()
    d, notes = look(llm=FakeLLM(simple), web=web)
    out = d.run(tiny_photo(), "image/png", "what colour is it?")
    assert web.searched == [] and not notes and "Direct answer from the picture" in out and "Mention that you checked online" not in out


def test_an_unknown_species_name_lowers_confidence():
    guess = {**PLANT, "candidates": [{"name": "Monsterus invented", "confidence": 0.8, "why": "x"}], "regions": []}
    d, _ = look(llm=FakeLLM(guess, verify={"match": 0.3}), web=FakeWeb(known=()))
    out = d.run(tiny_photo(), "image/png", "what plant is this?")
    assert "Confidence low" in out or "Confidence medium" in out
    assert "taxonomy 0.15" in out


def test_people_are_never_identified_and_documents_are_not_researched():
    person = {**PLANT, "category": "person", "candidates": [{"name": "Some Famous Person", "confidence": 0.9, "why": "face"}], "queries": ["who is this man"]}
    web = FakeWeb()
    d, _ = look(llm=FakeLLM(person), web=web)
    out = d.run(tiny_photo(), "image/png", "who is this?")
    assert "Some Famous Person" not in out and web.searched == []
    doc = {**PLANT, "category": "document", "candidates": [], "queries": ["invoice number 1234567"], "regions": []}
    web2 = FakeWeb()
    d2, _ = look(llm=FakeLLM(doc), web=web2)
    d2.run(tiny_photo(), "image/png", "summarise this")
    assert web2.searched == []


def test_search_terms_are_filtered_for_personal_data():
    q = D.clean_queries(["jane.doe@mail.com address", "call 555 123 4567 now", "https://x.y/z", "glossy split leaf houseplant", "ab", "x " * 40], "plant")
    assert q == ["glossy split leaf houseplant"]
    assert D.clean_queries(["anything useful here"], "document") == []
    assert D.clean_queries(["anything useful here"], "document", allow_private=True) == ["anything useful here"]


def test_plantnet_is_used_only_with_the_option_and_a_key():
    cfg = Config(allow_web=True, deep_vision=True, plantnet_enabled=True)
    d, _ = look(cfg=cfg, llm=FakeLLM(PLANT), plantnet_key=lambda: "k")
    assert "plantnet 0.93" in d.run(tiny_photo(), "image/png", "what plant?")
    for cfg2, key in ((Config(allow_web=True, plantnet_enabled=False), "k"), (cfg, "")):
        d2, _ = look(cfg=cfg2, llm=FakeLLM(PLANT), plantnet_key=lambda key=key: key)
        assert "plantnet" not in d2.run(tiny_photo(), "image/png", "what plant?")


def test_a_failing_web_does_not_break_the_answer():
    class Down(FakeWeb):
        def search(self, *a, **k):
            raise httpx.ConnectError("offline")

        def wikipedia(self, *a):
            raise httpx.ConnectError("offline")

        def taxon(self, *a):
            raise httpx.ConnectError("offline")

    d, _ = look(llm=FakeLLM(PLANT), web=Down())
    out = d.run(tiny_photo(), "image/png", "what plant is this?")
    assert "Best match: Monstera deliciosa" in out


def test_a_model_that_ignores_the_format_falls_back_to_plain():
    class Chatty:
        def stream_chat(self, messages):
            yield StreamEvent("text", "It is a plant.")

    d, _ = look(llm=Chatty())
    assert d.run(tiny_photo(), "image/png", "what is this") == ""


def test_json_is_found_inside_prose_and_fences():
    assert D.parse_json('Sure!\n```json\n{"a": {"b": 1}}\n```') == {"a": {"b": 1}}
    assert D.parse_json("no json here") == {}


def test_reference_photos_only_come_from_known_hosts():
    w = D.Web(client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x", headers={"content-type": "image/jpeg"}))))
    assert w.reference_image("https://evil.example.com/a.jpg") is None
    assert w.reference_image("http://upload.wikimedia.org/a.jpg") is None
    assert w.reference_image("https://upload.wikimedia.org/a.jpg") == b"x"


def test_web_clients_parse_the_public_apis():
    def handler(r: httpx.Request) -> httpx.Response:
        u = str(r.url)
        if "rest.php/v1/search/page" in u:
            return httpx.Response(200, json={"pages": [{"key": "Monstera_deliciosa"}]})
        if "page/summary" in u:
            return httpx.Response(200, json={"title": "Monstera deliciosa", "extract": "A plant.", "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/M"}},
                                             "thumbnail": {"source": "https://upload.wikimedia.org/x.jpg"}})
        if "api.inaturalist.org" in u:
            return httpx.Response(200, json={"results": [{"id": 5, "name": "Monstera deliciosa", "preferred_common_name": "Swiss cheese plant", "rank": "species",
                                                          "iconic_taxon_name": "Plantae", "default_photo": {"medium_url": "https://static.inaturalist.org/p.jpg"}}]})
        if "plantnet" in u:
            return httpx.Response(200, json={"results": [{"score": 0.9, "species": {"scientificNameWithoutAuthor": "Monstera deliciosa", "commonNames": ["Swiss cheese plant"],
                                                                                    "family": {"scientificNameWithoutAuthor": "Araceae"}}}]})
        return httpx.Response(404)

    w = D.Web(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert w.wikipedia("monstera")["extract"] == "A plant."
    t = w.taxon("monstera")
    assert t["common"] == "Swiss cheese plant" and t["url"].endswith("/5")
    assert w.plantnet("k", b"img", "image/jpeg")[0]["family"] == "Araceae"


def test_image_helpers_scale_and_crop():
    big = tiny_photo()
    out, mime = D.prepare(big, 100)
    assert mime == "image/jpeg" and out[:2] == b"\xff\xd8"
    assert D.crop(big, 0.1, 0.1, 0.5, 0.5)[:2] == b"\xff\xd8"
    assert D.crop(big, 0.1, 0.1, 0.001, 0.001) is None


def test_dropped_pictures_and_phone_photos_use_the_deep_look(tmp_path):
    from voice_assistant.attachments import Inbox
    from voice_assistant.phone_camera import build_phone_camera_tools

    pic = tmp_path / "plant.png"
    pic.write_bytes(tiny_photo())
    inbox = Inbox(tmp_path / "inbox")
    inbox.add(str(pic))
    name = next(iter(inbox.items))
    seen = []
    assert inbox.look(name, "what is this?", lambda: None, "English", deep=lambda d, m, q: seen.append((m, q)) or "DOSSIER") == "DOSSIER"
    assert seen == [("image/png", "what is this?")]

    class Remote:
        def request_photo(self, question, auto=False):
            return tiny_photo(), "ok"

    tool = build_phone_camera_tools(Config(), lambda: Remote(), lambda m: True, lambda: None, deep=lambda d, m, q: "PHONE DOSSIER")[0]
    assert tool.handler(question="which plant?") == "PHONE DOSSIER"
