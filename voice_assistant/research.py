"""Looking something up properly: search a few ways, read the best pages (not just the search snippets), and hand the model the passages that
answer the question, each with its site and the date the page was published or updated, numbered so the answer can say where it came from.

Only happens when "Use the internet" is on: the search words and the page addresses leave this PC, nothing else does. Everything that comes back
is someone else's writing, so it is marked as information, never instructions.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import urlparse

from .tools import Tool

MAX_QUERIES = 3
MAX_PAGES = 4
PASSAGES_PER_PAGE = 3
PASSAGE_CHARS = 420
PAGE_TIMEOUT_S = 8
_STOP = set("""a an and are as at be by can did do does for from has have how i in is it its me my of on or the this that to was were what when where which who
why will with you your about into than then there their they them tell find out look up please latest current today now""".split())
_DATE_KEYS = ("article:modified_time", "og:updated_time", "dateModified", "article:published_time", "datePublished", "pubdate", "citation_publication_date", "citation_date", "date")


def site_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9][a-z0-9'.-]*", text.lower()) if w not in _STOP and len(w) > 1]


def page_date(html: str) -> str:
    """When the page says it was published or last updated (YYYY-MM-DD), or ""."""
    found = []
    for key in _DATE_KEYS:
        for m in re.finditer(r"""(?:property|name|itemprop)\s*=\s*["']%s["'][^>]*?content\s*=\s*["']([^"']+)""" % re.escape(key), html, re.I):
            found.append(m.group(1))
        for m in re.finditer(r"""content\s*=\s*["']([^"']+)["'][^>]*?(?:property|name|itemprop)\s*=\s*["']%s["']""" % re.escape(key), html, re.I):
            found.append(m.group(1))
        for m in re.finditer(r'"%s"\s*:\s*"([^"]+)"' % re.escape(key), html):
            found.append(m.group(1))
    found += re.findall(r"""<time[^>]+datetime\s*=\s*["']([^"']+)""", html, re.I)[:3]
    dates = []
    for value in found:
        m = re.match(r"\s*(\d{4})[-/](\d{2})[-/](\d{2})", value)
        if m and 1995 <= int(m.group(1)) <= datetime.now().year + 1:
            dates.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
    return max(dates) if dates else ""


def readable(chunk: str) -> bool:
    """Prose, not code, commands, link lists or menus."""
    if "://" in chunk or "{" in chunk or chunk.count("/") > 6:
        return False
    letters = sum(ch.isalpha() or ch == " " for ch in chunk)
    return letters / max(len(chunk), 1) > 0.75 and len(chunk.split()) >= 6


def passages(text: str, question: str, n: int = PASSAGES_PER_PAGE) -> list[str]:
    """The parts of a page that best match the question, in the page's own order."""
    want = set(words(question))
    if not want:
        return []
    paras = [p.strip() for p in re.split(r"\n+", text) if len(p.strip()) > 40]
    chunks: list[str] = []
    for p in paras:  # long paragraphs become overlapping windows, so one fact is not split off its sentence
        if len(p) <= PASSAGE_CHARS:
            chunks.append(p)
        else:
            sentences = re.split(r"(?<=[.!?])\s+", p)
            cur = ""
            for s in sentences:
                if len(cur) + len(s) > PASSAGE_CHARS and cur:
                    chunks.append(cur.strip())
                    cur = ""
                cur += s + " "
            if cur.strip():
                chunks.append(cur.strip())
    scored = []
    for i, c in enumerate(chunks):
        if not readable(c):
            continue
        have = set(words(c))
        hits = sum(1 for w in want if w in have or any(h.startswith(w[:5]) for h in have if len(w) >= 5))
        if hits:
            numbers = 0.5 if re.search(r"\d", c) else 0  # facts are often numbers and dates
            scored.append((hits + numbers, i, c))
    best = sorted(scored, key=lambda x: -x[0])[:n]
    return [c[:PASSAGE_CHARS] for _s, _i, c in sorted(best, key=lambda x: x[1])]


def research(question: str, queries: list[str] | None = None, search=None, fetch=None, today: str = "") -> str:
    """search(query) -> [{title, href, body}], fetch(url) -> (html, content type, error). Both are the real web unless a test passes its own."""
    from .agent_tools import UNTRUSTED, fetch_raw, html_to_text

    if search is None:
        def search(q):
            from ddgs import DDGS

            return DDGS().text(q, max_results=6) or []
    fetch = fetch or (lambda url: fetch_raw(url, timeout=PAGE_TIMEOUT_S))
    question = str(question).strip()
    if not question:
        return "Error: say what to research."
    qs = [q for q in ([str(q).strip() for q in (queries or [])] + [question]) if q][:MAX_QUERIES]
    seen: dict[str, dict] = {}
    for q in dict.fromkeys(qs):  # results that come up for several wordings rank higher
        try:
            results = search(q)
        except Exception as e:
            results = []
            last_error = e.__class__.__name__
        for rank, r in enumerate(results):
            url = r.get("href") or r.get("url") or ""
            if not url.startswith(("http://", "https://")):
                continue
            entry = seen.setdefault(url, {"title": r.get("title", ""), "url": url, "snippet": r.get("body", ""), "score": 0.0})
            entry["score"] += 1.0 / (rank + 1)
    if not seen:
        return "No search results came back" + (f" ({last_error})" if "last_error" in locals() else "") + ". Say you could not look it up right now."
    ranked = sorted(seen.values(), key=lambda e: -e["score"])
    picked, sites = [], set()
    for e in ranked:  # one page per site, so the answer rests on several sources
        if site_name(e["url"]) not in sites:
            picked.append(e)
            sites.add(site_name(e["url"]))
        if len(picked) >= MAX_PAGES:
            break

    def read(e):
        body, ctype, err = fetch(e["url"])
        if err:
            return e, [], "", err
        text = html_to_text(body) if "html" in ctype else body
        return e, passages(text, question), page_date(body) if "html" in ctype else "", ""

    got = []
    with ThreadPoolExecutor(MAX_PAGES) as pool:
        for fut in as_completed([pool.submit(read, e) for e in picked]):
            try:
                got.append(fut.result())
            except Exception as ex:  # one page failing never spoils the rest
                got.append(({"title": "", "url": "", "snippet": ""}, [], "", str(ex)))
    order = {e["url"]: i for i, e in enumerate(picked)}
    got.sort(key=lambda g: order.get(g[0]["url"], 99))
    lines, n = [], 0
    for e, found, date, err in got:
        if not e["url"]:
            continue
        n += 1
        title = (e["title"] or site_name(e["url"]))[:100].replace("|", "/")
        lines.append(f"[{n}] {title} | {e['url']} | {site_name(e['url'])} | {'updated ' + date if date else 'date unknown'}")
        if found:
            lines += [f"    {p}" for p in found]
        else:
            lines.append(f"    (page not readable{': ' + err if err else ''}; search snippet) {e['snippet'][:300]}")
    today = today or datetime.now().strftime("%A %d %B %Y")
    return (UNTRUSTED + f"Research for: {question}\nToday is {today}.\n" + "\n".join(lines) + "\n\n"
            "How to answer: use only these sources for facts that can change. Name the site you relied on in plain words (\"according to Wikipedia\"); "
            "do not read web addresses aloud. If the fact can change over time, say how recent the source is. If sources disagree, say so and give both. "
            "If none of them answers the question, say you could not find it rather than guessing.")


def build_research_tools() -> list[Tool]:
    from .agent_tools import _obj, _str

    def research_tool(question: str, queries: list | None = None) -> str:
        return research(question, queries if isinstance(queries, list) else None)

    return [Tool(
        "research",
        "Look something up properly on the web: it searches (up to 3 wordings in queries), reads the best pages and returns the passages that answer the "
        "question, numbered, with each site and how recent it is. Use this for facts, news, prices, versions, opening hours, comparisons and anything "
        "that can change; use web_search only for a quick list of links.",
        _obj({"question": _str, "queries": {"type": "array", "items": _str}}, ["question"]), research_tool)]


RESEARCH_PROMPT = (
    " For questions about facts in the world, especially anything that can change (news, versions, prices, hours, records, who holds a role), call research "
    "instead of answering from memory, and answer from what it returns, naming the site you used."
)


def parse_sources(result: str) -> list[dict]:
    """The numbered sources in a research result, for the phone's answer card."""
    out = []
    for m in re.finditer(r"^\[(\d+)\] (.*?) \| (https?://\S+) \| ", str(result), re.M):
        out.append({"title": m.group(2), "url": m.group(3)})
    return out

