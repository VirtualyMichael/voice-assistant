"""Control YouTube Music through its web page (the Chrome DevTools Protocol), not by guessing at pixels.

YouTube Music is a web app, so its page has a search box, result rows and play buttons that code can find by name. The assistant keeps its own YouTube Music
window (a separate browser profile, so you sign in there once) and talks to it over a local debug connection: it searches, ranks the results by how well they
match what you asked for, clicks the play button of the best match and checks that the song really started. No screenshots, no vision model, no cost.

Why a separate window: browsers only allow this kind of control for a window started with a debug port, and Chrome refuses a debug port on the normal profile."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import quote_plus

import httpx

from .config import config_dir

START_URL = "https://music.youtube.com"
STOPWORDS = {"by", "the", "a", "an", "song", "music", "play", "from", "feat", "ft", "on", "in", "youtube", "and", "of"}
KINDS = {"song": 0.45, "video": 0.05, "single": 0.30}  # what a spoken "play X" almost always means
WANT_WORDS = {"album": "album", "ep": "ep", "playlist": "playlist", "mix": "playlist", "radio": "playlist"}


class YtmError(RuntimeError):
    pass


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9']+", text.lower().replace("&", " and ")) if t]


def kind_of(sub: str) -> tuple[str, str]:
    """('song', 'Stellardrone') from a subtitle like 'Song • Stellardrone • 3.1M plays'."""
    parts = [p.strip() for p in re.split(r"[••]", sub) if p.strip()]
    if not parts:
        return "", ""
    first = parts[0].lower()
    artist = parts[1] if len(parts) > 1 else ""
    artist = re.sub(r"\s+[\d.,]+\s*[KMB]?\s*(plays|views|subscribers|likes).*$", "", artist, flags=re.I).strip()  # "Stellardrone 3.1M plays" -> "Stellardrone"
    return first, artist


JUNK = {"lyrics", "lyric", "cover", "karaoke", "live", "remix", "instrumental", "reaction", "slowed", "reverb", "sped", "8d", "nightcore", "tutorial", "originally",
        "tribute", "version", "acoustic", "piano", "mashup", "bass", "boosted", "loop", "hour"}


def split_request(query: str) -> tuple[list[str], list[str]]:
    """'Maps by Maroon 5' -> (['maps'], ['maroon', '5']); without 'by' the artist part is empty and everything counts as the title."""
    parts = re.split(r"\s+by\s+", query, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        return [t for t in tokens(parts[0]) if t not in STOPWORDS] or tokens(parts[0]), [t for t in tokens(parts[1]) if t not in STOPWORDS] or tokens(parts[1])
    return [t for t in tokens(query) if t not in STOPWORDS] or tokens(query), []


class YtmNoMatch(YtmError):
    """The page has no result that matches what was asked for (so the wrong song must not be played instead)."""

    def __init__(self, message: str, closest: list[str] | None = None):
        super().__init__(message)
        self.closest = closest or []


def compact(text: str) -> str:
    return "".join(tokens(text))


def similarity(a: str, b: str) -> float:
    """0..1 how alike two names are with spaces ignored: 'SLVM Lord' and 'SLVMLORD' are the same name to a speech recogniser."""
    a, b = compact(a), compact(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0 if min(len(a), len(b)) >= 3 else 0.0
    return SequenceMatcher(None, a, b).ratio()


def rank(query: str, cands: list[dict]) -> list[tuple[float, dict]]:
    """Candidates best first (each gets `_title_hit` / `_artist_hit`, 0..1). Rewards a title that covers the requested song name and an artist that covers the
    requested artist (names compared with spaces ignored), prefers real songs, and penalises extra title words (so 'Eternity' beats 'Eternity (Reprise)'),
    lyric/karaoke/cover uploads, albums and playlists (unless asked for) and artist pages."""
    title_q, artist_q = split_request(query)
    all_q = set(title_q) | set(artist_q)
    wanted = {WANT_WORDS[t] for t in all_q if t in WANT_WORDS}
    title_q = [t for t in title_q if t not in WANT_WORDS] or title_q
    out = []
    for c in cands:
        kind, artist = kind_of(c.get("sub", ""))
        title_t, artist_t = set(tokens(c.get("title", ""))), set(tokens(artist))
        if not title_t or not (title_q or artist_q):
            continue
        t_cover = sum(t in title_t for t in title_q) / max(1, len(title_q)) if title_q else 0.0
        title_hit = max(t_cover, similarity(" ".join(title_q), c["title"])) if title_q else 0.0
        if artist_q:
            a_cover = sum(t in artist_t or t in title_t for t in artist_q) / len(artist_q)
            artist_hit = max(a_cover, similarity(" ".join(artist_q), artist) if artist else 0.0)
            artist_hit = artist_hit if artist_hit >= 0.75 else min(artist_hit, a_cover)  # a loose resemblance is not a match
            score = title_hit * 1.6 + artist_hit * 1.4
        else:  # just a name: it may be a title, an artist or both
            artist_hit = 0.0
            have = title_t | artist_t
            score = max(sum(t in have for t in title_q) / max(1, len(title_q)), similarity(" ".join(title_q), artist)) * 2.4
        extra = len(title_t - set(title_q) - set(artist_q)) / max(1, len(title_t))
        score -= extra * 0.5
        score += KINDS.get(kind, 0.0)
        score -= 0.6 * len((title_t & JUNK) - all_q)
        if kind in ("album", "ep", "playlist") and kind not in wanted:
            score -= 2.0
        if kind in wanted:
            score += 1.0
        if kind in ("artist", "profile") or not kind:
            score -= 1.5
        out.append((score, {**c, "_title_hit": title_hit, "_artist_hit": artist_hit}))
    return sorted(out, key=lambda x: -x[0])


def describe(c: dict) -> str:
    kind, artist = kind_of(c.get("sub", ""))
    return f"{c['title']} by {artist}" if artist else c["title"]


def choose(query: str, cands: list[dict], judge=None) -> dict:
    """The best candidate for the request. When the top result is shaky (the requested artist does not match, a weak title match, or two near-equal
    candidates) and a `judge` is given, the judge (a language model reading the whole list) decides. When nothing really matches, raises YtmNoMatch rather
    than playing a different song."""
    ranked = rank(query, cands)
    if not ranked:
        raise YtmNoMatch(f"No results for '{query}' showed up on the page.")
    score, best = ranked[0]
    _, artist_q = split_request(query)
    weak = score < 1.0 or best["_title_hit"] < 0.6 or (bool(artist_q) and best["_artist_hit"] < 0.6)
    close_call = len(ranked) > 1 and score - ranked[1][0] < 0.2 and describe(ranked[1][1]) != describe(best)
    if weak or close_call:
        pick = judge(query, [c for _, c in ranked[:12]]) if judge else None
        if pick is not None:
            return pick
        if weak:
            raise YtmNoMatch(f"Nothing in the results matched '{query}'.", [describe(c) for _, c in ranked[:5]])
    return best


def search_queries(query: str) -> list[str]:
    """The request as spoken, then the same words in 'artist title' order (YouTube Music's search often finds the right song that way)."""
    parts = re.split(r"\s+by\s+", query, maxsplit=1, flags=re.I)
    return [query] + ([f"{parts[1].strip()} {parts[0].strip()}"] if len(parts) == 2 and parts[0].strip() and parts[1].strip() else [])


def no_match_message(query: str, closest: list[str]) -> str:
    seen = list(dict.fromkeys(closest))[:5]
    return f"Could not find '{query}' on YouTube Music. Nothing played." + (" The closest results were: " + "; ".join(seen) + "." if seen else "")


JUDGE_SYSTEM = ("You match a spoken music request to YouTube Music search results. The request came from speech recognition, so names may be split, joined or misspelled "
                "(for example 'SLVM Lord' is the artist 'SLVMLORD'). Reply with ONLY the number of the result that is the requested song by the requested artist, "
                "or NONE if no result is both. Never pick a song with the right title but a different artist.")


def make_judge(get_llm):
    """A judge(query, candidates) -> candidate | None that asks the user's own language model (text only, no images) to read the whole result list."""

    def judge(query: str, cands: list[dict]):
        lines = [f"{i}. {c['title']} | {c.get('sub', '')}" for i, c in enumerate(cands)]
        messages = [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": f"Request: {query}\nResults:\n" + "\n".join(lines) + "\nAnswer:"}]
        try:
            text = "".join(ev.text for ev in get_llm().stream_chat(messages) if ev.kind == "text").strip()
        except Exception:
            return None
        if "NONE" in text.upper():
            return None
        m = re.search(r"\d+", text)
        return cands[int(m.group())] if m and int(m.group()) < len(cands) else None

    return judge


def started(best: dict, state: dict) -> str | None:
    """A 'Playing ...' sentence when the page is really playing the chosen song (not a stale queue entry), else None."""
    if state.get("paused") is False and state.get("title"):
        want = set(tokens(best["title"])) - STOPWORDS
        got = set(tokens(state["title"]))
        if want and len(want & got) / len(want) < 0.5:
            return None
        kind, artist = kind_of(best["sub"])
        what = f"{state['title']} by {artist}" if artist else state["title"]
        return f"Playing {what} (a {kind or 'result'}) in the assistant's YouTube Music window."
    return None


EXTRACT_JS = r"""(() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => !!el && el.getClientRects().length > 0 && el.offsetParent !== null;  // earlier searches stay in the page, hidden
  const cands = [];
  const card = [...document.querySelectorAll('ytmusic-card-shelf-renderer')].find(visible);
  if (card) {
    const pb = card.querySelector('ytmusic-play-button-renderer');
    const title = norm((card.querySelector('.title') || {}).innerText);
    const sub = norm((card.querySelector('.subtitle') || card.querySelector('.secondary-flex-columns') || {}).innerText);
    if (pb && title) cands.push({kind: 'card', i: 0, title, sub, aria: pb.getAttribute('aria-label') || ''});
  }
  [...document.querySelectorAll('ytmusic-responsive-list-item-renderer')].filter(visible).forEach((it, i) => {
    const pb = it.querySelector('ytmusic-play-button-renderer');
    if (!pb) return;
    cands.push({kind: 'row', i, title: norm((it.querySelector('.title') || {}).innerText),
                sub: norm((it.querySelector('.secondary-flex-columns') || {}).innerText), aria: pb.getAttribute('aria-label') || ''});
  });
  const box = document.querySelector('ytmusic-search-box input');
  return JSON.stringify({url: location.href, ready: document.readyState, q: new URL(location.href).searchParams.get('q') || '', cands});
})()"""

CLICK_JS = r"""((kind, i) => {
  const visible = el => !!el && el.getClientRects().length > 0 && el.offsetParent !== null;
  const el = kind === 'card' ? [...document.querySelectorAll('ytmusic-card-shelf-renderer')].find(visible)
                             : [...document.querySelectorAll('ytmusic-responsive-list-item-renderer')].filter(visible)[i];
  const pb = el && el.querySelector('ytmusic-play-button-renderer');
  if (!pb) return 'missing';
  pb.click();
  return 'clicked';
})(%s, %d)"""

STATE_JS = r"""(() => {
  const v = document.querySelector('video');
  const t = document.querySelector('ytmusic-player-bar .title');
  const by = document.querySelector('ytmusic-player-bar .byline');
  return JSON.stringify({paused: v ? v.paused : null, time: v ? v.currentTime : null, title: t ? t.innerText : '', by: by ? by.innerText.replace(/\s+/g, ' ') : ''});
})()"""


class Cdp:
    """A minimal DevTools Protocol client over one page's websocket."""

    def __init__(self, ws_url: str, connect=None):
        if connect is None:
            from websockets.sync.client import connect as _connect

            connect = _connect
        self.ws = connect(ws_url, max_size=None)
        self.n = 0

    def call(self, method: str, timeout: float = 15.0, **params) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        mine = self.n
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = json.loads(self.ws.recv(timeout=max(0.1, end - time.time())))
            except TimeoutError:
                break
            if msg.get("method") == "Page.javascriptDialogOpening":
                # YouTube Music asks "Leave app?" when you navigate away while a song plays: that dialog blocks the page until it is answered
                self.n += 1
                self.ws.send(json.dumps({"id": self.n, "method": "Page.handleJavaScriptDialog", "params": {"accept": True}}))
                continue
            if msg.get("id") == mine:
                if "error" in msg:
                    raise YtmError(f"{method}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})
        raise YtmError(f"{method}: no answer (is a dialog open in the YouTube Music window?)")

    def evaluate(self, js: str, gesture: bool = False):
        res = self.call("Runtime.evaluate", expression=js, returnByValue=True, awaitPromise=True, userGesture=gesture)
        if "exceptionDetails" in res:
            raise YtmError("page script failed: " + str(res["exceptionDetails"].get("exception", {}).get("description", ""))[:200])
        return res.get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def find_browser() -> str | None:
    """Chrome (preferred, it hosts the YouTube Music app for most people) or Edge."""
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(env)
        if base:
            for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
                p = Path(base) / rel
                if p.exists():
                    return str(p)
    return shutil.which("chrome") or shutil.which("msedge")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class YtmBrowser:
    def __init__(self, folder: Path | None = None, find=find_browser, popen=subprocess.Popen, http_get=httpx.get, connect=None,
                 sleep=time.sleep, clock=time.time, port_picker=free_port):
        self.folder = folder or config_dir() / "ytm-browser"
        self.find, self.popen, self.http_get, self.connect = find, popen, http_get, connect
        self.sleep, self.clock, self.port_picker = sleep, clock, port_picker
        self.cdp: Cdp | None = None

    # ---- connection ------------------------------------------------------------------
    @property
    def port_file(self) -> Path:
        return self.folder / "port.txt"

    def _page_ws(self, port: int) -> str | None:
        try:
            targets = self.http_get(f"http://127.0.0.1:{port}/json", timeout=2).json()
        except Exception:
            return None
        for t in targets:
            if t.get("type") == "page" and "music.youtube.com" in t.get("url", ""):
                return t.get("webSocketDebuggerUrl")
        return None

    def attach(self, timeout: float = 25.0) -> Cdp:
        """Connect to the assistant's YouTube Music window, opening it first if it is not running."""
        if self.cdp is not None:
            try:
                self.cdp.evaluate("1")
                return self.cdp
            except Exception:
                self.cdp = None
        port = None
        try:
            port = int(self.port_file.read_text().strip())
        except (OSError, ValueError):
            pass
        ws = self._page_ws(port) if port else None
        if ws is None:
            exe = self.find()
            if not exe:
                raise YtmError("Chrome or Edge was not found, so the YouTube Music window could not be opened.")
            port = self.port_picker()
            (self.folder / "profile").mkdir(parents=True, exist_ok=True)
            self.port_file.write_text(str(port))
            self.popen([exe, f"--user-data-dir={self.folder / 'profile'}", f"--remote-debugging-port={port}", f"--app={START_URL}",
                        "--no-first-run", "--no-default-browser-check"])
            end = self.clock() + timeout
            while ws is None and self.clock() < end:
                self.sleep(0.5)
                ws = self._page_ws(port)
            if ws is None:
                raise YtmError("The YouTube Music window did not open in time.")
            self.sleep(2.0)  # let the page settle before the first command
        self.cdp = Cdp(ws, self.connect)
        self.cdp.call("Page.enable")  # so the "Leave app?" dialog is reported and can be answered
        return self.cdp

    # ---- actions -----------------------------------------------------------------------
    def _wait_results(self, cdp: Cdp, query: str, timeout: float = 16.0) -> list[dict]:
        """Results for THIS query: the page's address must carry it (not the previous search still on screen), and the list must stop changing."""
        end = self.clock() + timeout
        previous = None
        while self.clock() < end:
            self.sleep(0.7)
            try:
                data = json.loads(cdp.evaluate(EXTRACT_JS) or "{}")
            except (YtmError, ValueError):
                continue
            cands = data.get("cands", [])
            if "search" not in data.get("url", "") or " ".join(tokens(data.get("q", ""))) != " ".join(tokens(query)) or len(cands) < 3:
                previous = None
                continue
            sig = [(c["kind"], c["i"], c["title"]) for c in cands]
            if sig == previous:  # two looks in a row, identical: the page has finished loading
                return cands
            previous = sig
        return []

    def play(self, query: str, judge=None, library=None) -> str:
        """Search YouTube Music for `query`, click play on the best match and confirm it started. Returns a sentence starting 'Playing' on success."""
        try:
            return self._play_once(query, judge, library)
        except YtmError:
            raise
        except Exception:  # the connection dropped (the window was closed or reloaded): reconnect once and try again
            if self.cdp is not None:
                self.cdp.close()
            self.cdp = None
            return self._play_once(query, judge, library)

    def _play_once(self, query: str, judge=None, library=None) -> str:
        cdp = self.attach()
        best, closest = None, []
        spelled = (library.resolve(query) if library else None) or query  # the saved library's own spelling of the song, when it is one of yours
        for q in search_queries(spelled):
            cdp.call("Page.navigate", url=f"{START_URL}/search?q={quote_plus(q)}")
            cands = self._wait_results(cdp, q)
            try:
                best = choose(spelled, cands, judge)
                break
            except YtmNoMatch as e:
                closest += e.closest
        if best is None:
            raise YtmNoMatch(no_match_message(query, closest), closest)
        state = {}
        for attempt in (1, 2):  # a click can be lost while the page is still settling: click once more before giving up
            res = cdp.evaluate(CLICK_JS % (json.dumps(best["kind"]), best["i"]), gesture=True)
            if res != "clicked":
                raise YtmError("The play button of the best match could not be found on the page.")
            end = self.clock() + 9
            while self.clock() < end:
                self.sleep(0.7)
                try:
                    state = json.loads(cdp.evaluate(STATE_JS) or "{}")
                except (YtmError, ValueError):
                    continue
                done = started(best, state)
                if done:
                    return done
        raise YtmError(f"I clicked play on '{best['title']}', but the page did not start playing that song (it shows '{state.get('title') or 'nothing'}').")

    def now_playing(self) -> str:
        state = json.loads(self.attach().evaluate(STATE_JS) or "{}")
        return f"{state.get('title') or 'nothing'} ({'paused' if state.get('paused') else 'playing'})"
