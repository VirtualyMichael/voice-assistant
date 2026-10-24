from voice_assistant.answer_cards import CardCollector
from voice_assistant.research import page_date, parse_sources, passages, readable, research

PAGE = """<html><head><meta property="article:modified_time" content="2026-10-20T08:00:00Z"><title>Eiffel Tower</title></head><body>
<nav>Home | News | Sport | Weather | Contact us</nav>
<p>The Eiffel Tower is a wrought-iron lattice tower on the Champ de Mars in Paris, France.</p>
<p>Since the addition of a new digital radio antenna in March 2022, the tower is 330 metres tall including its antennas.</p>
<p>pip install "tower @ https://example.com/pkg/tower-1.0.whl#sha256=abcdef0123456789"</p>
<p>The tower is the most visited monument with an entrance fee in the world.</p></body></html>"""


def fake_search(results):
    return lambda q: results.get(q, results.get("*", []))


def test_research_reads_the_best_page_of_each_site_and_dates_it():
    hits = [{"title": "Eiffel Tower - Wikipedia", "href": "https://en.wikipedia.org/wiki/Eiffel_Tower", "body": "snippet"},
            {"title": "Another Wikipedia page", "href": "https://en.wikipedia.org/wiki/Paris", "body": "x"},
            {"title": "Tower facts", "href": "https://facts.example/eiffel", "body": "facts snippet"}]
    fetched = []

    def fetch(url):
        fetched.append(url)
        return (PAGE, "text/html", "") if "wikipedia" in url else ("", "", "Error: HTTP 403")

    out = research("How tall is the Eiffel Tower including antennas?", search=fake_search({"*": hits}), fetch=fetch, today="Saturday 24 October 2026")
    assert sorted(fetched) == ["https://en.wikipedia.org/wiki/Eiffel_Tower", "https://facts.example/eiffel"]  # one page per site
    assert out.startswith("[Untrusted web content") and "Today is Saturday 24 October 2026" in out
    assert "[1] Eiffel Tower - Wikipedia | https://en.wikipedia.org/wiki/Eiffel_Tower | en.wikipedia.org | updated 2026-10-20" in out
    assert "330 metres tall including its antennas" in out
    assert "pip install" not in out and "Home | News" not in out  # code and menus are not passages
    assert "(page not readable: Error: HTTP 403; search snippet) facts snippet" in out
    assert "If sources disagree, say so" in out


def test_results_found_by_several_wordings_rank_first():
    a = {"title": "A", "href": "https://a.example/", "body": ""}
    b = {"title": "B", "href": "https://b.example/", "body": ""}
    out = research("q", ["one", "two"], search=fake_search({"one": [a, b], "two": [b], "q": [b]}), fetch=lambda u: ("<p>q q q answer here for you now</p>", "text/html", ""))
    assert out.index("https://b.example/") < out.index("https://a.example/")


def test_no_results_and_failures_are_said_plainly():
    assert "No search results" in research("anything", search=lambda q: [], fetch=lambda u: ("", "", ""))

    def broken(q):
        raise ConnectionError("down")

    assert "No search results came back (ConnectionError)" in research("anything", search=broken, fetch=lambda u: ("", "", ""))
    assert research("  ", search=lambda q: [], fetch=None).startswith("Error")


def test_dates_come_from_the_page_and_the_newest_wins():
    assert page_date('<meta content="2024-01-02" property="article:published_time"><meta property="article:modified_time" content="2025-06-07T10:00">') == "2025-06-07"
    assert page_date('<script>{"datePublished":"2023-03-04T00:00:00"}</script>') == "2023-03-04"
    assert page_date('<time datetime="2022-09-10">10 Sept</time>') == "2022-09-10"
    assert page_date("<p>no dates</p>") == "" and page_date('<meta property="date" content="1066-10-14">') == ""


def test_passages_pick_the_matching_prose():
    text = "Intro about Paris and France in general terms.\nThe tower stands 330 metres tall including antennas since 2022.\nUnrelated text about cheese and wine here."
    assert passages(text, "how tall is the tower")[0].startswith("The tower stands 330")
    assert not readable("curl https://x.example/install.sh | sh") and readable("The tower stands 330 metres tall today.")


def test_the_answer_card_lists_the_pages_research_read():
    out = research("tower height", search=fake_search({"*": [{"title": "Eiffel Tower", "href": "https://en.wikipedia.org/wiki/Eiffel_Tower", "body": ""}]}),
                   fetch=lambda u: (PAGE, "text/html", ""))
    assert parse_sources(out) == [{"title": "Eiffel Tower", "url": "https://en.wikipedia.org/wiki/Eiffel_Tower"}]
    c = CardCollector()
    c.user_final("q")
    c.tool("research", {"question": "tower height"}, out)
    c.assistant_delta("It is 330 metres, according to Wikipedia.")
    assert c.card()["sources"][0]["site"] == "en.wikipedia.org"
