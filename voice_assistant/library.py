"""A local reference library: folders of documents (textbooks, guidelines, manuals, notes, saved web pages, PDFs) that the assistant can search
instead of answering from memory. The text is split into passages and indexed with SQLite full-text search (BM25 ranking), so one question
finds the best passages across every document at once, each with the document, the page or heading, and the file's date.

Why this and not a bigger model: a small model that reads a reference passage answers from it; a small model that answers from memory guesses.
Folders are named after what is in them ("medicine", "python", "calculus"), and a question can be limited to one. Nothing leaves the PC.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .tools import Tool

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".json"}
HTML_SUFFIXES = {".html", ".htm"}
DOC_SUFFIXES = TEXT_SUFFIXES | HTML_SUFFIXES | {".pdf"}
MAX_FILE_BYTES = 300_000_000
MAX_TEXT_BYTES = 40_000_000
CHUNK_CHARS = 900
OVERLAP_CHARS = 150
MAX_RESULTS = 8
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+#.-]*")
_STOP = set("a an and are as at be by can do does for from how i in is it of on or the this that to was what when where which who why with you your".split())


def bundled_folder(name: str) -> Path | None:
    """A folder that ships with the program: beside the program when installed, in the project folder when run from source."""
    import sys

    bases = [Path(__file__).resolve().parent.parent]
    if getattr(sys, "frozen", False):
        bases.insert(0, Path(sys.executable).resolve().parent)
    for base in bases:
        if (base / name).is_dir():
            return base / name
    return None


@dataclass
class Hit:
    title: str
    collection: str
    where: str  # "p. 12" or "§ Heading"
    date: str  # the file's last-changed date
    text: str
    path: str


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = OVERLAP_CHARS) -> list[str]:
    """Passages that end at paragraph or sentence breaks where possible, overlapping a little so a fact is not cut off from its sentence."""
    paras = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur, fresh = "", False  # fresh: cur holds text that is not yet in any chunk (not just the overlap)

    def flush() -> None:
        nonlocal cur, fresh
        if fresh:
            chunks.append(cur.strip())
        cur, fresh = (cur[-overlap:] if overlap else ""), False

    for p in paras:
        for piece in ([p] if len(p) <= size else re.split(r"(?<=[.!?])\s+", p)):
            while len(piece) > size:  # one huge unbroken line
                flush()
                chunks.append(piece[:size])
                piece = piece[size - overlap:]
            if cur and len(cur) + len(piece) + 1 > size:
                flush()
            cur, fresh = (cur + " " + piece).strip(), True
    flush()
    return [c for c in chunks if len(c) >= 30]


def _sections(text: str) -> list[tuple[str, str]]:
    """[(heading, body)] for Markdown-ish text; one unnamed section when there are no headings."""
    parts = re.split(r"(?m)^(#{1,4} .+)$", text)
    if len(parts) == 1:
        return [("", text)]
    out = [("", parts[0])] if parts[0].strip() else []
    for i in range(1, len(parts), 2):
        out.append((parts[i].lstrip("# ").strip()[:80], parts[i + 1] if i + 1 < len(parts) else ""))
    return out


def extract(path: Path) -> list[tuple[str, str]]:
    """[(where, text)] units of a document: PDF pages, Markdown sections, or the whole text."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        out = []
        for i, page in enumerate(reader.pages, 1):
            try:
                out.append((f"p. {i}", page.extract_text() or ""))
            except Exception:  # one damaged page must not lose the rest
                continue
            time.sleep(0.001)  # pure-Python extraction holds the GIL: let audio callbacks run
        return out
    raw = path.read_text(encoding="utf-8", errors="replace")
    if suffix in HTML_SUFFIXES:
        from .agent_tools import html_to_text

        return [("", html_to_text(raw))]
    if suffix in (".md", ".markdown"):
        return [("§ " + h if h else "", body) for h, body in _sections(raw)]
    return [("", raw)]


def match_query(query: str) -> str:
    """An FTS5 query from plain words: every word optional (OR) so a long question still finds things, ranked by how many and how rare they are."""
    words = [w.strip(".-") for w in _WORD.findall(query.lower())]
    words = [w for w in words if w and w not in _STOP and len(w) > 1]
    return " OR ".join(f'"{w}"' for w in list(dict.fromkeys(words))[:12]) if words else ""


class Library:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.progress = ""  # what the background indexer is doing, for the settings page
        self.problems: list[str] = []
        self._db = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, path TEXT UNIQUE, collection TEXT, title TEXT, mtime REAL, size INTEGER, chunks INTEGER);"
            "CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(text, doc UNINDEXED, loc UNINDEXED, tokenize='porter unicode61');"
        )

    # ---- building the index ----------------------------------------------------------------------------------------------
    def add_folder(self, folder: str | Path, collection: str = "") -> tuple[int, int]:
        """Index every new or changed document under the folder. Returns (documents indexed now, documents unchanged)."""
        root = Path(folder)
        name = collection or root.name
        done = same = 0
        for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in DOC_SUFFIXES):
            try:
                st = path.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_BYTES or (path.suffix.lower() != ".pdf" and st.st_size > MAX_TEXT_BYTES):
                self.problems.append(f"{path.name} is too large to index.")
                continue
            with self._lock:
                row = self._db.execute("SELECT mtime, size, collection FROM docs WHERE path=?", (str(path),)).fetchone()
            if row and abs(row[0] - st.st_mtime) < 1 and row[1] == st.st_size and row[2] == name:
                same += 1
                continue
            self.progress = f"Indexing {path.name}…"
            try:
                units = extract(path)
            except Exception as e:
                self.problems.append(f"Could not read {path.name} ({e.__class__.__name__}).")
                continue
            self._store(path, name, units, st)
            done += 1
        self.progress = ""
        return done, same

    def _store(self, path: Path, collection: str, units: list[tuple[str, str]], st) -> None:
        with self._lock:
            old = self._db.execute("SELECT id FROM docs WHERE path=?", (str(path),)).fetchone()
            if old:
                self._db.execute("DELETE FROM passages WHERE doc=?", (old[0],))
                self._db.execute("DELETE FROM docs WHERE id=?", (old[0],))
            cur = self._db.execute("INSERT INTO docs(path, collection, title, mtime, size, chunks) VALUES(?,?,?,?,?,0)",
                                   (str(path), collection, path.stem.replace("_", " ").replace("-", " "), st.st_mtime, st.st_size))
            doc_id, count = cur.lastrowid, 0
            for where, text in units:
                for piece in chunk_text(text):
                    self._db.execute("INSERT INTO passages(text, doc, loc) VALUES(?,?,?)", (piece, doc_id, where))
                    count += 1
            self._db.execute("UPDATE docs SET chunks=? WHERE id=?", (count, doc_id))
            self._db.commit()

    def sync(self, folders: list[str]) -> None:
        """Index the given folders and forget documents whose files or folders are gone (so removing a folder removes its knowledge)."""
        self.problems = []
        roots = [Path(f) for f in folders]
        for root in roots:
            if root.is_dir():
                self.add_folder(root)
            else:
                self.problems.append(f"{root} was not found.")
        with self._lock:
            for doc_id, path in self._db.execute("SELECT id, path FROM docs").fetchall():
                p = Path(path)
                if not p.exists() or not any(r in p.parents for r in roots):
                    self._db.execute("DELETE FROM passages WHERE doc=?", (doc_id,))
                    self._db.execute("DELETE FROM docs WHERE id=?", (doc_id,))
            self._db.commit()

    def sync_in_background(self, folders: list[str]) -> threading.Thread:
        t = threading.Thread(target=lambda: self.sync(folders), daemon=True)
        t.start()
        return t

    # ---- asking -----------------------------------------------------------------------------------------------------------
    def collections(self) -> list[tuple[str, int, int]]:
        with self._lock:
            return self._db.execute("SELECT collection, COUNT(*), SUM(chunks) FROM docs GROUP BY collection ORDER BY collection").fetchall()

    def search(self, query: str, collection: str = "", k: int = 6) -> list[Hit]:
        q = match_query(query)
        if not q:
            return []
        sql = ("SELECT d.title, d.collection, p.loc, d.mtime, p.text, d.path FROM passages p JOIN docs d ON d.id = p.doc "
               "WHERE passages MATCH ?" + (" AND lower(d.collection) = lower(?)" if collection else "") + " ORDER BY bm25(passages) LIMIT ?")
        k = max(1, min(int(k), MAX_RESULTS))
        args: list = [q] + ([collection] if collection else []) + [k * 6]
        with self._lock:
            try:
                rows = self._db.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                return []
        asked = {w.lower() for w in _WORD.findall(query)}

        def named(title: str) -> bool:
            """The question names the document ('...in Discord' finds the Discord guide first, whatever else mentions voice chat)."""
            return any(len(w) >= 3 and w.lower() in asked for w in _WORD.findall(title) if w.lower() not in ("guide", "the", "and"))

        rows = sorted(rows, key=lambda r: not named(r[0]))  # stable: the search ranking is kept inside each group
        return [Hit(t, c, loc or "", time.strftime("%Y-%m-%d", time.localtime(m)), text, path) for t, c, loc, m, text, path in rows[:k]]

    def close(self) -> None:
        with self._lock:
            self._db.close()


def format_hits(query: str, hits: list[Hit], collection: str = "") -> str:
    from .agent_tools import UNTRUSTED

    if not hits:
        scope = f" in the '{collection}' collection" if collection else ""
        return f"Nothing in the reference library{scope} matches '{query}'. Try other words, or answer from general knowledge and say it is not from the library."
    lines = [UNTRUSTED + f"Reference library passages for: {query}"]
    for n, h in enumerate(hits, 1):
        lines.append(f"[{n}] {h.title} ({h.collection}{', ' + h.where if h.where else ''}; file dated {h.date})\n    {h.text[:900]}")
    lines.append("\nHow to answer: base the answer on these passages and name the document (and page) you used. If the passages do not answer the question, say so instead of guessing. "
                 "Check the file date: guidance in an old document may be out of date, so say how old it is when it matters.")
    return "\n".join(lines)


def build_library_tools(get_library) -> list[Tool]:
    from .agent_tools import _obj, _str

    def library_search(query: str, collection: str = "") -> str:
        lib = get_library()
        if lib is None:
            return "The reference library is not set up."
        return format_hits(query, lib.search(query, collection, k=6), collection)

    def library_list() -> str:
        lib = get_library()
        rows = lib.collections() if lib else []
        if not rows:
            return "The reference library is empty. The user can add folders of documents in Settings, Skills and library."
        return "Reference collections: " + "; ".join(f"{c} ({n} documents)" for c, n, _chunks in rows)

    return [
        Tool("library_search", "Search the user's local reference library (textbooks, guidelines, manuals, notes, saved pages) for passages that answer a question, across every document at once. "
             "Use it before answering from memory for medical, science, maths, coding and technical questions, or whenever the user refers to their documents. collection limits the search to one named collection.",
             _obj({"query": _str, "collection": _str}, ["query"]), library_search),
        Tool("library_list", "List the collections in the reference library and how many documents each has.", _obj({}, []), library_list),
    ]


LIBRARY_PROMPT = (
    " The user keeps a local reference library. For questions in medicine, science, maths, coding or any technical field, or when they mention their documents or notes, call "
    "library_search first and answer from the passages it returns, naming the document; fall back to research or your own knowledge only when it finds nothing relevant. "
    "For how-to questions about an app or program, search the 'app guides' collection and put the app's name in the query (for example 'OBS record screen') so its guide "
    "comes first; give the steps in order with the exact button names, and if the guide does not cover it or the user's screen differs, say so instead of guessing."
)


def folder_signature(folders: list[str]) -> str:
    return hashlib.sha1("|".join(sorted(folders)).encode()).hexdigest()[:10]
