"""Deep research: take a hard question apart, look in the right places, read the pages, keep only claims whose quote really is on the page,
check the claims against each other, and hand the assistant an evidence report with how well each finding is supported.

Everything runs on the local model; only search words and page addresses leave this PC (and only with "Use the internet" on). It is slower
than `research` (minutes, not seconds) and meant for questions where being right matters: health, science, money, comparisons, "what is the
current evidence on...".

The steps, each of which can fail without ruining the rest:
  1. Plan:    the model splits the question into sub-questions with search words, and says what kind of question it is.
  2. Gather:  web search, plus PubMed / arXiv / Wikipedia for medical, science and maths questions; the best page per site is read.
  3. Rate:    every source gets a tier (official or peer-reviewed, established reference or news, general site, forum or social).
  4. Extract: the model pulls claims out of each page with an exact quote; a claim whose quote is not on the page is dropped (so it cannot
              invent a source), and the dropped count is reported.
  5. Compare: claims that say the same thing are grouped; groups are marked well supported (independent sites, one a good source),
              single source, or conflicting.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote_plus, urlparse

from .research import page_date, passages, site_name
from .tools import Tool

MAX_SUBQUESTIONS = 4
MAX_SOURCES = 10
PAGES_PER_SUBQUESTION = 3
PASSAGES_PER_SOURCE = 5
BUDGET_S = 300

TIER_LABELS = {4: "official or peer-reviewed", 3: "established reference or news", 2: "general website", 1: "forum, social or blog"}
_TIER4_SUFFIX = (".gov", ".edu", ".mil", ".int", ".ac.uk", ".gov.uk", ".nhs.uk")
_TIER4 = {"nih.gov", "who.int", "cdc.gov", "nature.com", "science.org", "thelancet.com", "nejm.org", "bmj.com", "arxiv.org", "doi.org", "ieee.org", "acm.org",
          "springer.com", "sciencedirect.com", "cochranelibrary.com", "mayoclinic.org", "jamanetwork.com", "pnas.org", "cell.com", "plos.org", "nist.gov",
          "python.org", "kernel.org", "rust-lang.org", "w3.org", "ietf.org", "iso.org", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "medlineplus.gov"}
_TIER3 = {"wikipedia.org", "britannica.com", "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "npr.org", "nytimes.com", "wsj.com", "theguardian.com",
          "bloomberg.com", "washingtonpost.com", "economist.com", "ft.com", "developer.mozilla.org", "docs.python.org", "learn.microsoft.com", "developer.android.com",
          "stackoverflow.com", "khanacademy.org", "wolframalpha.com", "mathworld.wolfram.com", "healthline.com", "webmd.com", "clevelandclinic.org", "hopkinsmedicine.org"}
_TIER1 = {"reddit.com", "quora.com", "facebook.com", "x.com", "twitter.com", "tiktok.com", "pinterest.com", "medium.com", "blogspot.com", "wordpress.com",
          "answers.com", "ehow.com", "tumblr.com", "substack.com", "yahoo.com", "ask.com", "fandom.com", "youtube.com"}
SPECIALIST = {"medical": ("pubmed", "wikipedia"), "science": ("pubmed", "arxiv", "wikipedia"), "math": ("wikipedia", "arxiv")}


def source_tier(url: str) -> int:
    """1 to 4, from the site alone: a rough guide to how far to trust a source, never a verdict on one page."""
    host = site_name(url)

    def under(names) -> bool:
        return any(host == n or host.endswith("." + n) for n in names)

    if host.endswith(_TIER4_SUFFIX) or under(_TIER4):
        return 4
    if under(_TIER1):
        return 1
    if under(_TIER3):
        return 3
    return 2


_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}


def organisation(url: str) -> str:
    """The site's owner as far as the address says: www.cdc.gov, archive.cdc.gov and cdc.gov are all cdc.gov; bbc.co.uk stays bbc.co.uk."""
    labels = site_name(url).split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    keep = 3 if len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


@dataclass
class Source:
    n: int
    title: str
    url: str
    text: str = ""
    date: str = ""
    error: str = ""

    @property
    def site(self) -> str:
        return site_name(self.url)

    @property
    def tier(self) -> int:
        return source_tier(self.url)


@dataclass
class Claim:
    id: int
    sub: int
    text: str
    quote: str
    source: int  # Source.n


@dataclass
class Report:
    question: str
    subquestions: list[str]
    sources: list[Source]
    groups: list[dict] = field(default_factory=list)  # {"sub", "statement", "claims": [Claim], "status", "sites", "best_tier", "newest"}
    dropped: int = 0
    unverified_fallback: bool = False
    notes: list[str] = field(default_factory=list)


# ---- specialist places to look -------------------------------------------------------------------------------------------
def wikipedia_search(query: str, fetch) -> list[dict]:
    body, _ctype, err = fetch("https://en.wikipedia.org/w/api.php?action=opensearch&limit=3&format=json&search=" + quote_plus(query))
    if err:
        return []
    try:
        _q, titles, descs, urls = json.loads(body)
    except (ValueError, TypeError):
        return []
    return [{"title": t, "href": u, "body": d} for t, d, u in zip(titles, descs, urls)]


def pubmed_search(query: str, fetch) -> list[dict]:
    body, _ctype, err = fetch("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&retmax=4&sort=relevance&term=" + quote_plus(query))
    if err:
        return []
    try:
        ids = json.loads(body)["esearchresult"]["idlist"]
    except (ValueError, KeyError, TypeError):
        return []
    return [{"title": f"PubMed {i}", "href": f"https://pubmed.ncbi.nlm.nih.gov/{i}/", "body": ""} for i in ids]


def arxiv_search(query: str, fetch) -> list[dict]:
    body, _ctype, err = fetch("http://export.arxiv.org/api/query?max_results=3&search_query=all:" + quote_plus(query))
    if err:
        return []
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", body, re.S):
        link = re.search(r"<id>\s*(https?://arxiv\.org/abs/[^<\s]+)", entry)
        title = re.search(r"<title>(.*?)</title>", entry, re.S)
        summary = re.search(r"<summary>(.*?)</summary>", entry, re.S)
        if link:
            out.append({"title": " ".join((title.group(1) if title else "arXiv paper").split()), "href": link.group(1),
                        "body": " ".join((summary.group(1) if summary else "").split())[:400]})
    return out


def specialist_results(kind: str, query: str, fetch) -> list[dict]:
    out: list[dict] = []
    for name in SPECIALIST.get(kind, ()):
        try:
            out += {"pubmed": pubmed_search, "arxiv": arxiv_search, "wikipedia": wikipedia_search}[name](query, fetch)
        except Exception:
            continue
    return out


# ---- talking to the model --------------------------------------------------------------------------------------------------
def _json_from(text: str):
    """The first JSON object or array in a model's reply (models wrap it in prose or code fences), or None."""
    text = str(text or "")
    pairs = sorted(((text.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if o in text))  # whichever opens first is the outer one
    for a, _opener, closer in pairs:
        b = text.rfind(closer)
        if 0 <= a < b:
            try:
                return json.loads(text[a:b + 1])
            except ValueError:
                continue
    return None


PLAN_PROMPT = (
    "You plan web research. Split the user's question into 2 to 4 sub-questions that together answer it, each with 1 or 2 short web search queries. "
    "Also say what kind of question it is: medical, science, math, code, news or general. Reply with JSON only: "
    '{"kind":"medical","subquestions":[{"q":"...","queries":["...","..."]}]}'
)
CLAIM_PROMPT = (
    "From the passages of ONE web page, list the facts that help answer the question. Each fact needs an exact quote copied word for word from the passages "
    "(at least 8 words) that supports it. Do not add anything the page does not say. If nothing helps, return an empty list. Reply with JSON only: "
    '[{"claim":"short statement in your own words","quote":"exact words from the passages"}]'
)
COMPARE_PROMPT = (
    "Below are numbered claims found on different web pages. Group claims that say the same thing. If claims in a group contradict each other (different "
    "numbers, opposite advice), put them in the same group with \"conflict\":true. Reply with JSON only: "
    '[{"statement":"what the group says, or the disagreement","ids":[1,4],"conflict":false}]. Every id must appear in exactly one group.'
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def quote_is_on_page(quote: str, page_text: str) -> bool:
    """Is the claimed quote really there? Case, spacing and punctuation do not matter, but the words and their order do."""
    q, page = _norm(quote), _norm(page_text)
    return len(q.split()) >= 5 and q in page


# ---- the pipeline ----------------------------------------------------------------------------------------------------------
def deep_research(question: str, search, fetch, ask, notify=lambda s: None, clock=time.monotonic, budget_s: float = BUDGET_S, today: str = "") -> Report:
    """search(query) -> [{title, href, body}]; fetch(url) -> (body, content type, error); ask(system, user) -> the model's reply text."""
    from .agent_tools import html_to_text

    start = clock()

    def out_of_time() -> bool:
        return clock() - start > budget_s

    question = str(question).strip()
    report = Report(question, [], [])
    # 1. plan
    notify("Planning the research…")
    plan = _json_from(ask(PLAN_PROMPT, question)) if question else None
    subs: list[tuple[str, list[str]]] = []
    kind = "general"
    if isinstance(plan, dict):
        kind = str(plan.get("kind", "general")).lower()
        for item in (plan.get("subquestions") or [])[:MAX_SUBQUESTIONS]:
            if isinstance(item, dict) and str(item.get("q", "")).strip():
                subs.append((str(item["q"]).strip(), [str(x).strip() for x in (item.get("queries") or []) if str(x).strip()][:2]))
    if not subs:
        subs = [(question, [question])]
        report.notes.append("The question could not be split up, so it was researched as one piece.")
    report.subquestions = [q for q, _ in subs]

    # 2. gather
    notify(f"Searching ({len(subs)} parts)…")
    picked: list[tuple[int, dict]] = []  # (sub index, result); a page can serve several sub-questions but is read once
    unique_urls: set[str] = set()
    for si, (sq, queries) in enumerate(subs):
        seen: dict[str, dict] = {}
        for qn, query in enumerate(dict.fromkeys(queries or [sq])):
            try:
                results = list(search(query)) + (specialist_results(kind, query, fetch) if qn == 0 else [])
            except Exception:
                results = []
            for rank, r in enumerate(results):
                url = r.get("href") or r.get("url") or ""
                if url.startswith(("http://", "https://")):
                    e = seen.setdefault(url, {"title": r.get("title", ""), "url": url, "snippet": r.get("body", ""), "score": 0.0})
                    e["score"] += 1.0 / (rank + 1)
        for e in seen.values():
            e["score"] += 0.35 * (source_tier(e["url"]) - 2)  # better sites first, weak ones last
        orgs: set[str] = set()
        count = 0
        for e in sorted(seen.values(), key=lambda e: -e["score"]):
            if organisation(e["url"]) in orgs or source_tier(e["url"]) == 1 and count >= 1:
                continue
            if e["url"] not in unique_urls and len(unique_urls) >= MAX_SOURCES:
                continue
            picked.append((si, e))
            unique_urls.add(e["url"])
            orgs.add(organisation(e["url"]))
            count += 1
            if count >= PAGES_PER_SUBQUESTION:
                break
    if not picked:
        report.notes.append("No search results came back.")
        return report

    notify(f"Reading {len(unique_urls)} pages…")
    firsts = {e["url"]: e for _si, e in reversed(picked)}

    def read(url: str):
        e = firsts[url]
        body, ctype, err = fetch(url)
        if err:
            return e, "", "", err
        is_html = "html" in (ctype or "")
        return e, html_to_text(body) if is_html else body, page_date(body) if is_html else "", ""

    with ThreadPoolExecutor(6) as pool:
        fetched = list(pool.map(read, list(dict.fromkeys(e["url"] for _si, e in picked))))
    source_of: dict[str, Source] = {}
    for n, (e, text, date, err) in enumerate(fetched, 1):
        src = Source(n, (e["title"] or site_name(e["url"]))[:100].replace("|", "/"), e["url"], text, date, err)
        report.sources.append(src)
        source_of[e["url"]] = src
    by_sub: dict[int, list[Source]] = {}
    for si, e in picked:
        by_sub.setdefault(si, []).append(source_of[e["url"]])

    # 4. extract claims, each with a quote that must be on the page
    claims: list[Claim] = []
    for si, srcs in by_sub.items():
        for src in srcs:
            if not src.text:
                continue
            found = passages(src.text, report.subquestions[si], PASSAGES_PER_SOURCE)
            if not found:
                continue
            if out_of_time():
                claims += [Claim(len(claims) + 1, si, p[:200], p, src.n) for p in found[:2]]  # no time to ask: the passages stand in as claims
                report.unverified_fallback = True
                continue
            notify(f"Reading {src.site}…")
            raw = _json_from(ask(CLAIM_PROMPT, f"Question: {report.subquestions[si]}\n\nPassages:\n" + "\n".join(f"- {p}" for p in found)))
            if not isinstance(raw, list):
                claims += [Claim(len(claims) + 1, si, p[:200], p, src.n) for p in found[:2]]
                report.unverified_fallback = True
                continue
            for item in raw[:5]:
                if not isinstance(item, dict):
                    continue
                claim, quote = str(item.get("claim", "")).strip(), str(item.get("quote", "")).strip()
                if claim and quote_is_on_page(quote, src.text):
                    claims.append(Claim(len(claims) + 1, si, claim, quote, src.n))
                else:
                    report.dropped += 1
    if not claims:
        report.notes.append("No page gave a usable, checkable fact.")
        return report

    # 5. compare
    notify("Comparing what the sources say…")
    src_of = {s.n: s for s in report.sources}
    groups_raw = None
    if not out_of_time() and len(claims) > 1:
        listing = "\n".join(f"{c.id}. [{src_of[c.source].site}] {c.text}" for c in claims)
        groups_raw = _json_from(ask(COMPARE_PROMPT, f"Question: {question}\n\nClaims:\n{listing}"))
    claim_by_id = {c.id: c for c in claims}
    placed: set[int] = set()
    grouped: list[tuple[str, list[Claim], bool]] = []
    if isinstance(groups_raw, list):
        for g in groups_raw:
            if not isinstance(g, dict):
                continue
            members = [claim_by_id[i] for i in g.get("ids", []) if isinstance(i, int) and i in claim_by_id and i not in placed]
            if members:
                placed.update(c.id for c in members)
                grouped.append((str(g.get("statement") or members[0].text), members, bool(g.get("conflict"))))
    for c in claims:  # anything the model left out stands alone
        if c.id not in placed:
            grouped.append((c.text, [c], False))
    for statement, members, conflict in grouped:
        sites = sorted({organisation(src_of[c.source].url) for c in members})  # cdc.gov and archive.cdc.gov are one voice, not two
        best = max(src_of[c.source].tier for c in members)
        dates = [src_of[c.source].date for c in members if src_of[c.source].date]
        if conflict and len(sites) > 1:
            status = "sources disagree"
        elif len(sites) >= 3 or (len(sites) >= 2 and best >= 3):
            status = "well supported"
        elif len(sites) >= 2:
            status = "supported by more than one site, none of them top-tier"
        else:
            status = "one source only"
        report.groups.append({"sub": members[0].sub, "statement": statement, "claims": members, "status": status, "sites": sites, "best_tier": best,
                              "newest": max(dates) if dates else ""})
    order = {"well supported": 0, "sources disagree": 1, "supported by more than one site, none of them top-tier": 2, "one source only": 3}
    report.groups.sort(key=lambda g: (g["sub"], order[g["status"]], -g["best_tier"]))
    return report


def format_report(report: Report, today: str = "") -> str:
    from .agent_tools import UNTRUSTED

    today = today or datetime.now().strftime("%A %d %B %Y")
    lines = [UNTRUSTED + f"Deep research for: {report.question}", f"Today is {today}."]
    if report.notes:
        lines += report.notes
    if not report.groups:
        lines.append("Nothing checkable was found. Say you could not research this properly right now.")
    for si, sq in enumerate(report.subquestions):
        mine = [g for g in report.groups if g["sub"] == si]
        if not mine:
            continue
        lines.append(f"\n## {sq}")
        for g in mine:
            cites = ", ".join(f"[{n}]" for n in sorted({c.source for c in g["claims"]}))
            when = f", newest {g['newest']}" if g["newest"] else ""
            lines.append(f"- {g['statement']}  ({g['status']}; {len(g['sites'])} site(s); best source: {TIER_LABELS[g['best_tier']]}{when}) {cites}")
    lines.append("\nSources:")
    for s in report.sources:
        lines.append(f"[{s.n}] {s.title} | {s.url} | {s.site} | {'updated ' + s.date if s.date else 'date unknown'} | {TIER_LABELS[s.tier]}" + (f" (could not be read: {s.error})" if s.error else ""))
    lines.append(f"\nChecked: {sum(len(g['claims']) for g in report.groups)} claims were verified against the page text; {report.dropped} were dropped because their quote was not on the page."
                 + (" Some claims are unchecked page passages because time ran out or the model did not follow the format." if report.unverified_fallback else ""))
    lines.append("How to answer: give the direct answer first, in plain words, then what is well supported and what is not. Name the sites you relied on (\"according to the WHO\"); "
                 "never read web addresses aloud. Say plainly where sources disagree and where only one source says something. For health, money or safety questions add that this is "
                 "information, not professional advice. Do not add facts that are not in this report.")
    return "\n".join(lines)


def build_deep_research_tools(get_llm, notify=lambda s: None, language=lambda: "English") -> list[Tool]:
    from .agent_tools import _obj, _str, fetch_raw

    def ask(system: str, user: str) -> str:
        messages = [{"role": "system", "content": system + f" Answer in {language()} only where the JSON text is for the user; keep JSON keys in English."},
                    {"role": "user", "content": user}]
        return "".join(ev.text for ev in get_llm().stream_chat(messages) if ev.kind == "text")

    def search(query: str):
        from ddgs import DDGS

        return DDGS().text(query, max_results=6) or []

    def run(question: str) -> str:
        report = deep_research(question, search, lambda url: fetch_raw(url, timeout=10), ask, notify)
        return format_report(report)

    return [Tool(
        "deep_research",
        "Research a hard question thoroughly, which takes a few minutes: it splits the question up, searches the web and (for health, science and maths) PubMed, arXiv and Wikipedia, reads many pages, "
        "keeps only facts whose exact quote is on the page, compares sources and says how well supported each finding is. Use it when being right matters or the user asks for deep research, a "
        "thorough comparison, or 'what does the evidence say'. Tell the user it will take a few minutes first. For a quick fact use research instead.",
        _obj({"question": _str}, ["question"]), run)]


DEEP_RESEARCH_PROMPT = (
    " For hard or important questions (health, science, money, big comparisons, 'what does the evidence say') or when the user asks for deep research, call deep_research "
    "after telling them it takes a few minutes, and answer from its report, including how well supported each point is."
)
