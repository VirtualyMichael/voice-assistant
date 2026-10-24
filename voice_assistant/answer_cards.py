"""Answer cards: what an answer was based on, sent to the phone so it can show it under the reply.

A card lists the web pages an answer came from (tap to open), pictures found online (downloaded and shrunk here, so the phone never contacts
those sites itself), and the settings the answer was about (tap to open them in the phone app). It is built by watching the tools the
assistant used during the turn, so the model does not have to format anything; links written in the reply itself are added too.
"""
from __future__ import annotations

import base64
import logging
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .session import SessionEvents
from .tools import Tool

log = logging.getLogger(__name__)
MAX_SOURCES = 6
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 3_000_000  # a thumbnail; anything bigger is skipped
THUMB_EDGE = 480
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_RESULT = re.compile(r"^\s*\d+\.\s+(.*?)\s+-\s+(https?://\S+)\s*$")


def site_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


class CardCollector(SessionEvents):
    """Listens to one turn at a time: the tools used (via ToolRegistry listeners) and the reply text (as a session listener)."""

    def __init__(self, fetch_image=None):
        self.fetch_image = fetch_image  # url -> JPEG bytes or None; None turns pictures off
        self._lock = threading.Lock()
        self._reset()

    def _reset(self) -> None:
        self.sources: list[dict] = []  # {title, url, read}
        self.images: list[dict] = []  # {thumb, page, caption}
        self.settings: list[dict] = []  # {key, section, label}
        self.text: list[str] = []

    # ---- the turn, as the session reports it ----------------------------------------
    def user_final(self, text: str) -> None:
        with self._lock:
            self._reset()

    def assistant_delta(self, text: str) -> None:
        with self._lock:
            self.text.append(text)

    # ---- the tools, as the registry reports them ------------------------------------
    def tool(self, name: str, args: dict, result: str) -> None:
        log.debug("card sees tool %s", name)
        with self._lock:
            if name == "web_search":
                for line in str(result).splitlines():
                    m = _RESULT.match(line)
                    if m:
                        self._source(m.group(1), m.group(2), read=False)
            elif name == "fetch_page" and not str(result).startswith("Error"):
                self._source("", str(args.get("url", "")), read=True)
            elif name == "research":
                from .research import parse_sources

                for s in parse_sources(result):
                    self._source(s["title"], s["url"], read=True)
            elif name == "find_images":
                for line in str(result).splitlines():
                    parts = [p.strip() for p in line.split(" | ")]
                    if len(parts) == 4 and parts[0].isdigit():
                        _n, caption, thumb, page = parts
                        if len(self.images) < MAX_IMAGES and thumb.startswith("http"):
                            self.images.append({"thumb": thumb, "page": page, "caption": caption[:80]})
            elif name in ("find_setting", "change_setting"):
                from .remote_settings import BY_KEY, SECTIONS, search_catalog

                found = [BY_KEY[args["key"]]] if name == "change_setting" and args.get("key") in BY_KEY else search_catalog(str(args.get("query", "")))[:2]
                titles = {sid: title for sid, title, _ in SECTIONS}
                for s in found:
                    if all(x["key"] != s.key for x in self.settings):
                        self.settings.append({"key": s.key, "section": s.section, "label": f"{titles.get(s.section, s.section)} › {s.label}"})

    def _source(self, title: str, url: str, read: bool) -> None:
        if not url.startswith(("http://", "https://")):
            return
        for s in self.sources:
            if s["url"] == url:
                s["read"] = s["read"] or read
                s["title"] = s["title"] or title
                return
        self.sources.append({"title": title, "url": url, "read": read})

    # ---- the card ----------------------------------------------------------------
    def card(self) -> dict | None:
        """The card for the turn that just ended, without pictures (see pictures()), or None when there is nothing to show."""
        with self._lock:
            answer = "".join(self.text)
            for url in _URL.findall(answer):
                self._source("", url.rstrip(".,;:"), read=True)
            mentioned = {site_name(u) for u in _URL.findall(answer)}
            # pages it read or linked first, then search results whose site the reply names, then the rest of the results
            ranked = sorted(self.sources, key=lambda s: (not s["read"], site_name(s["url"]) not in mentioned and site_name(s["url"]).split(".")[0] not in answer.lower()))
            sources = [{"title": s["title"] or site_name(s["url"]), "url": s["url"], "site": site_name(s["url"])} for s in ranked[:MAX_SOURCES]]
            settings = [{"section": s["section"], "label": s["label"]} for s in self.settings[:3]]
            pending = list(self.images)
        if not (sources or settings or pending):
            return None
        return {"type": "card", "id": uuid.uuid4().hex[:12], "sources": sources, "settings": settings, "images": [],
                "_pending": pending}

    def pictures(self, pending: list[dict]) -> list[dict]:
        """Download and shrink the pictures (slow: call off the event thread)."""
        if not pending or self.fetch_image is None:
            return []

        def one(p):
            try:
                jpeg = self.fetch_image(p["thumb"])
            except Exception:
                return None
            if not jpeg:
                return None
            return {"jpeg_b64": base64.b64encode(jpeg).decode(), "page": p["page"], "caption": p["caption"], "site": site_name(p["page"])}

        with ThreadPoolExecutor(MAX_IMAGES) as pool:
            return [x for x in pool.map(one, pending[:MAX_IMAGES]) if x]


def fetch_thumbnail(url: str, resolver=None) -> bytes | None:
    """A small JPEG of a picture on a public website, or None. Only public addresses are fetched and only a few MB are read."""
    import httpx

    from .agent_tools import USER_AGENT, public_host

    resolver = resolver or public_host
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or not resolver(u.hostname):
        return None
    with httpx.Client(timeout=6, follow_redirects=False, headers={"User-Agent": USER_AGENT}) as client, client.stream("GET", url) as r:
        if r.status_code >= 300 or not r.headers.get("content-type", "").startswith("image/"):
            return None
        data = b""
        for chunk in r.iter_bytes():
            data += chunk
            if len(data) > MAX_IMAGE_BYTES:
                return None
    return shrink_jpeg(data)


def shrink_jpeg(data: bytes, edge: int = THUMB_EDGE) -> bytes | None:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
    from PySide6.QtGui import QImage

    img = QImage.fromData(data)
    if img.isNull():
        return None
    if max(img.width(), img.height()) > edge:
        img = img.scaled(edge, edge, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out = QByteArray()
    buf = QBuffer(out)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPEG", 80)
    return bytes(out)


def build_image_tools() -> list[Tool]:
    from .agent_tools import UNTRUSTED, _obj, _str

    def find_images(query: str, max_results: int = 4) -> str:
        from ddgs import DDGS

        results = DDGS().images(query, max_results=max(1, min(int(max_results), 6)))
        if not results:
            return "No pictures found."
        rows = [f"{i} | {str(r.get('title', ''))[:80].replace('|', '/')} | {r.get('thumbnail') or r.get('image')} | {r.get('url', '')}" for i, r in enumerate(results, 1)]
        return (UNTRUSTED + "Pictures found (number | title | picture | page). They are shown to the user on their phone under your answer; "
                "describe what they show only from the titles, and do not read out the links.\n" + "\n".join(rows))

    return [Tool("find_images", "Find pictures online ('show me what a capybara looks like', 'what does that logo look like'). The pictures appear on the "
                                "user's phone under your answer, so just say briefly what you found.", _obj({"query": _str, "max_results": {"type": "integer"}}, ["query"]),
                 find_images)]
