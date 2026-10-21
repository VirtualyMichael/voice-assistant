import json
import time

import pytest

from voice_assistant.ytm_browser import YtmError
from voice_assistant.ytm_library import STALE_S, Library

LIB = {"fetchedAt": time.time() * 1000, "playlists": [
    {"id": "LM", "title": "Liked Music", "tracks": [
        {"videoId": "a1", "title": "Continuum", "artist": "SLVMLORD", "album": "x"},
        {"videoId": "a2", "title": "Lord of Nothing", "artist": "Sub Urban", "album": "y"},
        {"videoId": "a3", "title": "Fluxxwave (Super Slowed)", "artist": "Clovis Reyes", "album": ""},
    ]},
    {"id": "P2", "title": "likeish", "tracks": [
        {"videoId": "a1", "title": "Continuum", "artist": "SLVMLORD", "album": "x"},  # the same song in a second playlist: counted once
        {"videoId": "b1", "title": "Continuum", "artist": "Lemaitre", "album": "z"},
        {"videoId": "b2", "title": "Eternity", "artist": "Stellardrone", "album": ""},
        {"videoId": "b3", "title": "Eternity (Reprise)", "artist": "Stellardrone", "album": ""},
        {"videoId": "b4", "title": "Maps", "artist": "Maroon 5", "album": ""},
    ]},
]}


@pytest.fixture
def lib(tmp_path):
    L = Library(tmp_path / "lib.json")
    L.save(LIB)
    return L


def test_counts_and_summary_and_persistence(lib, tmp_path):
    assert lib.counts() == (2, 7) and "7 songs in 2 playlists" in lib.summary()
    assert Library(tmp_path / "lib.json").counts() == (2, 7)  # reloaded from disk
    assert Library(tmp_path / "none.json").summary() == "No library saved yet." and Library(tmp_path / "none.json").stale()


def test_spoken_names_are_put_into_the_librarys_own_spelling(lib):
    assert lib.resolve("Continuum by SLVM Lord") == "Continuum by SLVMLORD"
    assert lib.resolve("Lord of Nothing by Suburban") == "Lord of Nothing by Sub Urban"
    assert lib.resolve("play maps by maroon five") is None or lib.resolve("play maps by maroon five") == "Maps by Maroon 5"
    assert lib.resolve("Maps by Maroon 5") == "Maps by Maroon 5"
    assert lib.resolve("Eternity by Stellardrone") == "Eternity by Stellardrone"  # not the Reprise


def test_the_right_artist_wins_when_two_songs_share_a_title(lib):
    assert lib.resolve("Continuum by Lemaitre") == "Continuum by Lemaitre"
    assert lib.resolve("Continuum by SLVM Lord") == "Continuum by SLVMLORD"
    assert lib.resolve("Continuum") is None  # two different Continuums and no artist: the search page decides


def test_things_not_in_the_library_are_left_alone(lib):
    assert lib.resolve("Bohemian Rhapsody by Queen") is None
    assert lib.resolve("Continuum by Metallica") is None  # right title, wrong artist: never rewritten into someone else's song


def test_staleness(lib):
    assert not lib.stale() and lib.stale(now=time.time() + STALE_S + 60)


def test_refresh_saves_what_the_extension_sends_and_reports_unreadable_lists(tmp_path):
    class Bridge:
        def request(self, action, timeout=0, **kw):
            assert action == "library" and timeout >= 120
            data = dict(LIB)
            data["playlists"] = LIB["playlists"] + [{"id": "P3", "title": "broken", "tracks": [], "error": "YouTube Music answered 500"}]
            return {"ok": True, "library": data}

    L = Library(tmp_path / "x.json")
    out = L.refresh(Bridge())
    assert "7 songs in 3 playlists" in out and "1 playlist(s) could not be read" in out and json.loads((tmp_path / "x.json").read_text(encoding="utf-8"))["playlists"][0]["title"] == "Liked Music"

    class Empty:
        def request(self, *a, **k):
            return {"ok": True, "library": {"playlists": []}}

    with pytest.raises(YtmError, match="signed in"):
        Library(tmp_path / "y.json").refresh(Empty())


def test_a_huge_message_from_the_extension_fits_through_the_bridge():
    import threading

    from websockets.sync.client import connect

    from voice_assistant.ytm_bridge import YtmBridge

    b = YtmBridge(ports=(0,))
    b.start()
    port = b._server.socket.getsockname()[1]
    big = {"ok": True, "library": {"fetchedAt": 1, "playlists": [{"id": "x", "title": "t", "tracks": [{"videoId": "v", "title": "song" * 20, "artist": "a" * 30}] * 20000}]}}

    def ext():
        with connect(f"ws://127.0.0.1:{port}", additional_headers={"Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"}, max_size=None) as ws:
            ws.send(json.dumps({"type": "hello", "version": "1"}))
            msg = json.loads(ws.recv())
            ws.send(json.dumps({"id": msg["id"], **big}))
            time.sleep(1)

    threading.Thread(target=ext, daemon=True).start()
    end = time.time() + 3
    while time.time() < end and not b.connected:
        time.sleep(0.02)
    try:
        res = b.request("library", timeout=20)
        assert len(res["library"]["playlists"][0]["tracks"]) == 20000  # about 3 MB: would have been refused with the default 1 MB limit
    finally:
        b.stop()


PLAYLISTS = {"fetchedAt": time.time() * 1000, "playlists": [
    {"id": "PLearly", "title": "Early Morning", "tracks": [{"videoId": "1", "title": "Dance With Me", "artist": "A"}, {"videoId": "2", "title": "Bad Idea", "artist": "B"}]},
    {"id": "PLmix", "title": "Changes mix", "tracks": []},
    {"id": "PLmix2", "title": "Changes mix 2", "tracks": []},
]}


def test_stylised_names_are_matched_by_how_they_sound(tmp_path):
    from voice_assistant.ytm_browser import similarity

    assert similarity("Slum Lord", "SLVMLORD") >= 0.95 and similarity("S L V M lord", "SLVMLORD") == 1.0 and similarity("Beyonc\u00e9", "Beyonce") == 1.0
    assert similarity("Stellardrone", "Lemaitre") < 0.5
    L = Library(tmp_path / "l.json")
    L.save(LIB)
    assert L.resolve("Continuum by Slum Lord") == "Continuum by SLVMLORD"


def test_saved_playlists_are_found_by_name_or_refused(tmp_path):
    L = Library(tmp_path / "p.json")
    L.save(PLAYLISTS)
    assert L.find_playlist("the early morning playlist")[0]["id"] == "PLearly" and L.find_playlist("changes mix 2")[0]["id"] == "PLmix2"
    pl, closest = L.find_playlist("workout hits")
    assert pl is None and "Early Morning" in closest


def test_playlist_player_plays_only_a_saved_playlist_and_checks_the_song(tmp_path):
    from voice_assistant.ytm_bridge import YtmBridge, make_playlist_player
    from voice_assistant.ytm_browser import YtmNoMatch

    L = Library(tmp_path / "q.json")
    L.save(PLAYLISTS)
    b = YtmBridge(ports=(0,))
    opened = []
    b._ws = object()  # "connected"

    def request(action, timeout=0, **kw):
        if action == "open_playlist":
            opened.append(kw["playlist"])
            return {"ok": True}
        return {"ok": True, "state": {"paused": False, "title": "Bad Idea", "by": "B"}}

    b.request = request
    b.ensure_tab = lambda *a, **k: None
    play = make_playlist_player(b, L)
    out = b.play_playlist("early morning", L, sleep=lambda s: None)
    assert out.startswith("Playing your playlist 'Early Morning', starting with Bad Idea") and opened == ["PLearly"]
    assert "Could not find a playlist called" in play("workout hits") and opened == ["PLearly"]  # nothing opened for an unknown name
    b.request = lambda action, timeout=0, **kw: {"ok": True, "state": {"paused": False, "title": "Unrelated Song", "by": ""}} if action == "state" else {"ok": True}
    t = iter(range(10_000))
    with pytest.raises(Exception, match="did not start playing it"):
        b.play_playlist("early morning", L, sleep=lambda s: None, clock=lambda: next(t))
