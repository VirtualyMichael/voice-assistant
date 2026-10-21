import json

import pytest

from voice_assistant.config import Config
from voice_assistant.music import build_music_tools
from voice_assistant.ytm_browser import Cdp, YtmBrowser, YtmError, kind_of, rank, tokens

RESULTS = [
    {"kind": "card", "i": 0, "title": "Eternity", "sub": "Video \u2022 Stellardrone \u2022 2.1M views \u2022 6:02", "aria": "Play Eternity - Stellardrone"},
    {"kind": "row", "i": 0, "title": "Eternity (Reprise)", "sub": "Song \u2022 Stellardrone \u2022 322K plays", "aria": "Play Eternity (Reprise) - Stellardrone"},
    {"kind": "row", "i": 1, "title": "Eternity", "sub": "Song \u2022 Stellardrone 3.1M plays", "aria": "Play Eternity - Stellardrone"},
    {"kind": "row", "i": 2, "title": "Light Years", "sub": "Album \u2022 Stellardrone \u2022 2013", "aria": "Play Light Years"},
    {"kind": "row", "i": 3, "title": "Stellardrone", "sub": "Artist \u2022 1.2M subscribers", "aria": "Play Stellardrone"},
    {"kind": "row", "i": 4, "title": "Eternity (Orchestral)", "sub": "Song \u2022 Alex Warren \u2022 9M plays", "aria": "Play Eternity (Orchestral) - Alex Warren"},
]


def test_tokens_and_kind():
    assert tokens("Maps by Maroon 5!") == ["maps", "by", "maroon", "5"] and tokens("Rock & Roll") == ["rock", "and", "roll"]
    assert kind_of("Song \u2022 Stellardrone \u2022 3.1M plays") == ("song", "Stellardrone") and kind_of("") == ("", "")


def test_best_match_is_the_plain_song_by_the_right_artist():
    best = rank("Eternity Stellardrone", RESULTS)[0][1]
    assert best["kind"] == "row" and best["i"] == 1  # the Song, not the Video card, the Reprise, the album, the artist or another artist's Eternity
    assert rank("Eternity by Alex Warren", RESULTS)[0][1]["title"] == "Eternity (Orchestral)"
    assert rank("the album Light Years by Stellardrone", RESULTS)[0][1]["title"] == "Light Years"
    assert rank("Eternity Reprise", RESULTS)[0][1]["title"] == "Eternity (Reprise)"


def test_a_request_nothing_matches_scores_low():
    ranked = rank("zebra crossing blues", RESULTS)
    assert not ranked or ranked[0][0] < 1.0


class FakePage:
    """Plays the part of the YouTube Music page behind a Cdp object."""

    def __init__(self, cands, start_playing=True, click_result="clicked"):
        self.cands, self.start_playing, self.click_result = cands, start_playing, click_result
        self.q, self.now_title = "", "Eternity"
        self.navigated, self.clicks, self.playing = [], [], False

    def call(self, method, timeout=15.0, **params):
        if method == "Page.navigate":
            self.navigated.append(params["url"])
            self.q = params["url"].split("q=")[1].replace("+", "%20").replace("%20", " ")
        return {}

    def evaluate(self, js, gesture=False):
        if "cands.push" in js:
            return json.dumps({"url": "https://music.youtube.com/search?q=" + self.q, "q": self.q, "ready": "complete", "cands": self.cands})
        if "pb.click()" in js:
            self.clicks.append(js.rsplit("(", 1)[-1])
            self.playing = self.start_playing
            return self.click_result
        if "paused:" in js:
            return json.dumps({"paused": not self.playing, "time": 1.0, "title": self.now_title, "by": "Stellardrone"})
        return 1


def browser_with(page):
    b = YtmBrowser(folder=None, sleep=lambda s: None, clock=iter(range(0, 10000)).__next__)
    b.attach = lambda: page
    return b


def test_play_searches_clicks_the_best_row_and_confirms():
    page = FakePage(RESULTS)
    out = browser_with(page).play("Eternity Stellardrone")
    assert page.navigated == ["https://music.youtube.com/search?q=Eternity+Stellardrone"] and out.startswith("Playing Eternity by Stellardrone (a song)")
    assert page.clicks == ['"row", 1)']  # row index 1


def test_play_fails_loudly_instead_of_claiming_success():
    with pytest.raises(YtmError, match="did not start"):
        browser_with(FakePage(RESULTS, start_playing=False)).play("Eternity Stellardrone")
    with pytest.raises(YtmError, match="could not be found"):
        browser_with(FakePage(RESULTS, click_result="missing")).play("Eternity Stellardrone")
    with pytest.raises(YtmError, match="Could not find .anything."):
        browser_with(FakePage([])).play("anything")
    with pytest.raises(YtmError, match="Could not find"):
        browser_with(FakePage(RESULTS)).play("zebra crossing blues")


def test_attach_reuses_a_running_window_and_launches_one_otherwise(tmp_path):
    launched = []

    class Resp:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    state = {"up": False}

    def http_get(url, timeout=2):
        if not state["up"]:
            raise OSError("closed")
        return Resp([{"type": "page", "url": "https://music.youtube.com/", "webSocketDebuggerUrl": "ws://127.0.0.1:9999/x"}])

    class WS:
        def send(self, *a):
            pass

        def close(self):
            pass

        def recv(self, timeout=None):
            return json.dumps({"id": 1, "result": {"result": {"value": 1}}})

    def popen(cmd):
        launched.append(cmd)
        state["up"] = True

    b = YtmBrowser(tmp_path, find=lambda: "C:/chrome.exe", popen=popen, http_get=http_get, connect=lambda url, **k: WS(), sleep=lambda s: None, port_picker=lambda: 9999)
    cdp = b.attach()
    assert isinstance(cdp, Cdp) and len(launched) == 1
    cmd = launched[0]
    assert cmd[0] == "C:/chrome.exe" and "--remote-debugging-port=9999" in cmd and "--app=https://music.youtube.com" in cmd
    assert any(a.startswith("--user-data-dir=") and "profile" in a for a in cmd)  # its own profile, never the normal one
    b2 = YtmBrowser(tmp_path, find=lambda: "C:/chrome.exe", popen=popen, http_get=http_get, connect=lambda url, **k: WS(), sleep=lambda s: None)
    b2.attach()
    assert len(launched) == 1  # the port file led it to the window that is already open
    no_browser = YtmBrowser(tmp_path / "x", find=lambda: None, http_get=lambda *a, **k: (_ for _ in ()).throw(OSError()), sleep=lambda s: None)
    with pytest.raises(YtmError, match="not found"):
        no_browser.attach()


def test_play_music_uses_the_page_first_and_falls_back_when_it_fails():
    calls = []
    t = build_music_tools(Config(), lambda p: True, lambda: [], launcher=lambda: None, resolver=lambda q: None,
                          browser_play=lambda q: calls.append(q) or "Playing X by Y (a song) in the assistant's YouTube Music window.")[0].handler
    assert t("x by y").startswith("Playing X by Y") and calls == ["x by y"]

    def boom(q):
        raise YtmError("Chrome or Edge was not found")

    t2 = build_music_tools(Config(), lambda p: True, lambda: [], launcher=lambda: None, resolver=lambda q: None, browser_play=boom)[0].handler
    out = t2("x by y")
    assert "could not do it" in out and "Could not find a song" in out  # fell through to the older routes, and said so


def test_something_else_playing_is_not_reported_as_success():
    page = FakePage(RESULTS)
    page.now_title = "Obama - 15"
    with pytest.raises(YtmError, match="did not start playing that song"):
        browser_with(page).play("Eternity Stellardrone")


def test_stale_results_from_the_previous_search_are_ignored():
    page = FakePage(RESULTS)
    original = page.evaluate
    calls = {"n": 0}

    def evaluate(js, gesture=False):
        if "cands.push" in js:
            calls["n"] += 1
            if calls["n"] <= 3:  # the page still shows the earlier search for a moment
                return json.dumps({"url": "https://music.youtube.com/search?q=old+song", "q": "old song", "ready": "complete", "cands": [dict(RESULTS[2], title="Old Song")]})
        return original(js, gesture)

    page.evaluate = evaluate
    out = browser_with(page).play("Eternity Stellardrone")
    assert out.startswith("Playing Eternity") and page.clicks == ['"row", 1)']


def test_leave_app_dialog_is_accepted_so_navigation_is_not_blocked():
    sent = []
    incoming = iter([
        json.dumps({"method": "Page.javascriptDialogOpening", "params": {"type": "beforeunload"}}),
        json.dumps({"id": 1, "result": {"frameId": "x"}}),
    ])

    class WS:
        def send(self, data):
            sent.append(json.loads(data))

        def recv(self, timeout=None):
            return next(incoming)

        def close(self):
            pass

    cdp = Cdp("ws://x", connect=lambda url, **k: WS())
    assert cdp.call("Page.navigate", url="https://music.youtube.com/search?q=x") == {"frameId": "x"}
    assert sent[0]["method"] == "Page.navigate" and sent[1] == {"id": 2, "method": "Page.handleJavaScriptDialog", "params": {"accept": True}}


def test_a_silent_page_gives_a_clear_error_not_a_raw_timeout():
    class WS:
        def send(self, data):
            pass

        def recv(self, timeout=None):
            raise TimeoutError("timed out")

        def close(self):
            pass

    with pytest.raises(YtmError, match="no answer"):
        Cdp("ws://x", connect=lambda url, **k: WS()).call("Runtime.evaluate", timeout=0.2)


def test_the_official_song_beats_a_lyrics_video_by_another_uploader():
    cands = [
        {"kind": "card", "i": 0, "title": "MAPS - MAROON 5 (LYRICS)", "sub": "Video \u2022 All Lyrics \u2022 8M views", "aria": ""},
        {"kind": "row", "i": 1, "title": "Maps", "sub": "Song \u2022 Maroon 5 \u2022 1.2B plays", "aria": ""},
        {"kind": "row", "i": 2, "title": "Maps (Originally by Maroon 5) [Karaoke]", "sub": "Song \u2022 Karaoke Hits 770 plays", "aria": ""},
        {"kind": "row", "i": 3, "title": "Maroon 5", "sub": "Artist \u2022 30M subscribers", "aria": ""},
    ]
    assert rank("Maps by Maroon 5", cands)[0][1]["i"] == 1
    assert rank("Maps", cands)[0][1]["i"] == 1
    assert rank("Maps lyrics", cands)[0][1]["i"] == 0  # asked for the lyrics video: junk words are only penalised when not requested


def test_a_dropped_connection_is_retried_once_with_a_fresh_one():
    page = FakePage(RESULTS)
    b = YtmBrowser(folder=None, sleep=lambda s: None, clock=iter(range(0, 10000)).__next__)
    attempts = {"n": 0}

    def attach():
        attempts["n"] += 1
        if attempts["n"] == 1:
            class Dead:
                def call(self, *a, **k):
                    raise ConnectionError("no close frame received or sent")

                def close(self):
                    pass

            b.cdp = Dead()
            return b.cdp
        return page

    b.attach = attach
    assert b.play("Eternity Stellardrone").startswith("Playing Eternity") and attempts["n"] == 2


def test_a_lost_click_is_repeated_once():
    page = FakePage(RESULTS)
    plays = iter([False, True])
    original = page.evaluate

    def evaluate(js, gesture=False):
        if "pb.click()" in js:
            page.clicks.append("x")
            page.playing = next(plays)
            return "clicked"
        return original(js, gesture)

    page.evaluate = evaluate
    assert browser_with(page).play("Eternity Stellardrone").startswith("Playing") and len(page.clicks) == 2


SLVM = [
    {"kind": "card", "i": 0, "title": "Orbiting Something Undefined", "sub": "Song • SLVMLORD • 3:14", "aria": ""},
    {"kind": "row", "i": 0, "title": "SEARCH HISTORY", "sub": "Song • SLVMLORD • 586K plays", "aria": ""},
    {"kind": "row", "i": 1, "title": "Residual Presence", "sub": "Song • SLVMLORD • 40K plays", "aria": ""},
    {"kind": "row", "i": 2, "title": "Continuum", "sub": "Song • Lemaitre • 2.1M plays", "aria": ""},
    {"kind": "row", "i": 3, "title": "a playlist for wandering without purpose :)", "sub": "Video • SLVMLORD • 96K views", "aria": ""},
]
WITH_SLVM = SLVM + [{"kind": "row", "i": 5, "title": "Continuum", "sub": "Song • SLVMLORD • 1.2M plays", "aria": ""}]


def test_a_spoken_artist_name_split_in_two_matches_the_one_word_name():
    from voice_assistant.ytm_browser import similarity

    assert similarity("SLVM Lord", "SLVMLORD") == 1.0 and similarity("Maroon 5", "Maroon5") == 1.0 and similarity("Sub Urban", "Suburban") == 1.0
    assert similarity("Stellardrone", "Lemaitre") < 0.5
    assert rank("Continuum by SLVM Lord", WITH_SLVM)[0][1]["sub"].startswith("Song • SLVMLORD") and rank("Continuum by SLVM Lord", WITH_SLVM)[0][1]["title"] == "Continuum"


def test_the_wrong_artist_is_never_played_just_because_the_title_matches():
    from voice_assistant.ytm_browser import YtmNoMatch, choose

    with pytest.raises(YtmNoMatch) as e:
        choose("Continuum by SLVM Lord", SLVM)  # the only Continuum on the page is by Lemaitre
    assert any("Lemaitre" in c for c in e.value.closest)


def test_the_model_can_pick_from_the_whole_list_when_the_ranking_is_unsure():
    from voice_assistant.ytm_browser import choose, make_judge

    asked = []

    def judge(query, cands):
        asked.append([c["title"] for c in cands])
        return next(c for c in cands if c["title"] == "Residual Presence")

    assert choose("Continuum by SLVM Lord", SLVM, judge)["title"] == "Residual Presence" and asked
    assert choose("Continuum by SLVM Lord", WITH_SLVM, judge)["sub"].find("SLVMLORD") > 0  # a sure match never needs the model

    class Ev:
        def __init__(self, t):
            self.kind, self.text = "text", t

    class LLM:
        def __init__(self, answer):
            self.answer = answer

        def stream_chat(self, messages, tools=None, cancel=None):
            self.seen = messages
            yield Ev(self.answer)

    llm = LLM("2")
    assert make_judge(lambda: llm)("x", SLVM)["title"] == "Residual Presence" and "SLVMLORD" in llm.seen[0]["content"]
    assert make_judge(lambda: LLM("NONE"))("x", SLVM) is None and make_judge(lambda: LLM("99"))("x", SLVM) is None


def test_play_tries_artist_then_title_order_and_reports_honestly_when_nothing_matches():
    from voice_assistant.ytm_browser import YtmNoMatch, search_queries

    assert search_queries("Continuum by SLVM Lord") == ["Continuum by SLVM Lord", "SLVM Lord Continuum"] and search_queries("Maps") == ["Maps"]
    page = FakePage(SLVM)
    with pytest.raises(YtmNoMatch, match="Could not find 'Continuum by SLVM Lord'.*Nothing played.*Continuum by Lemaitre"):
        browser_with(page).play("Continuum by SLVM Lord")
    assert len(page.navigated) == 2 and not page.clicks  # both orders searched, nothing clicked
    page2 = FakePage(SLVM)
    out_calls = []
    page2.original = page2.evaluate

    def second_search_finds_it(js, gesture=False):
        if "cands.push" in js and len(page2.navigated) == 2:
            return json.dumps({"url": "https://music.youtube.com/search?q=" + page2.q, "q": page2.q, "ready": "complete", "cands": WITH_SLVM})
        return page2.original(js, gesture)

    page2.evaluate = second_search_finds_it
    page2.now_title = "Continuum"
    assert browser_with(page2).play("Continuum by SLVM Lord").startswith("Playing Continuum")
    assert page2.clicks == ['"row", 5)']
