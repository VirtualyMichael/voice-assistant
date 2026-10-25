"""Deep research with a fake web and a fake model, so the checks (quotes must be on the page, sources compared and rated) are exact."""
import json

from voice_assistant import deep_research as dr

PAGE_WHO = ("<html><head><meta property='article:modified_time' content='2025-03-02T10:00:00Z'></head><body><p>"
            "Adults should get at least 150 minutes of moderate-intensity aerobic activity every week according to the guideline. "
            "This amount lowers the risk of heart disease in most adults who follow it.</p></body></html>")
PAGE_NIH = "<html><body><p>Adults need at least 150 minutes of moderate exercise per week, and strength training on two days. Both help keep the heart healthy.</p></body></html>"
PAGE_BLOG = "<html><body><p>Some bloggers say that you only need 30 minutes of exercise per month to be perfectly healthy and live forever without any other effort.</p></body></html>"
PAGES = {
    "https://www.who.int/exercise": PAGE_WHO,
    "https://www.nih.gov/exercise": PAGE_NIH,
    "https://healthtips.example.com/post": PAGE_BLOG,
}


def fetch(url):
    if url in PAGES:
        return PAGES[url], "text/html", ""
    return "", "", "not found"


def search(query):
    return [{"title": "WHO", "href": "https://www.who.int/exercise", "body": ""},
            {"title": "Tips", "href": "https://healthtips.example.com/post", "body": ""},
            {"title": "NIH", "href": "https://www.nih.gov/exercise", "body": ""}]


def model(plan=None, claims=None, groups=None):
    """A fake model that answers each of the three kinds of request."""
    calls = []

    def ask(system, user):
        calls.append(system[:20])
        if system.startswith("You plan"):
            return json.dumps(plan if plan is not None else {"kind": "medical", "subquestions": [{"q": "How much exercise do adults need each week?", "queries": ["adult exercise minutes per week"]}]})
        if system.startswith("From the passages"):
            site_claims = claims(user) if callable(claims) else claims
            return json.dumps(site_claims if site_claims is not None else [])
        if system.startswith("Below are numbered"):
            return json.dumps(groups(user) if callable(groups) else groups or [])
        return ""

    ask.calls = calls
    return ask


def claims_by_page(user):
    if "bloggers" in user:
        return [{"claim": "Only 30 minutes per month is needed", "quote": "you only need 30 minutes of exercise per month to be perfectly healthy"}]
    if "strength training" in user:
        return [{"claim": "Adults need 150 minutes of moderate exercise a week", "quote": "Adults need at least 150 minutes of moderate exercise per week"}]
    return [{"claim": "Adults need 150 minutes of moderate activity a week", "quote": "at least 150 minutes of moderate-intensity aerobic activity every week"},
            {"claim": "The guideline was approved by the moon", "quote": "the moon approved this guideline in a secret ceremony"}]  # not on the page


def run(**kw):
    return dr.deep_research("How much exercise do adults need?", search, fetch, model(claims=claims_by_page, **kw))


def test_sites_are_rated_by_how_far_to_trust_them():
    assert dr.source_tier("https://www.who.int/x") == 4 and dr.source_tier("https://pubmed.ncbi.nlm.nih.gov/123/") == 4
    assert dr.source_tier("https://cdc.gov/x") == 4 and dr.source_tier("https://www.ox.ac.uk/") == 4 and dr.source_tier("https://www.stanford.edu/a") == 4
    assert dr.source_tier("https://en.wikipedia.org/wiki/X") == 3 and dr.source_tier("https://random-shop.example.com") == 2
    assert dr.source_tier("https://www.reddit.com/r/x") == 1 and dr.source_tier("https://old.reddit.com/r/x") == 1


def test_a_quote_has_to_be_on_the_page_word_for_word():
    page = "Adults should get at least 150 minutes of moderate-intensity aerobic activity every week."
    assert dr.quote_is_on_page("at least 150 minutes of moderate intensity aerobic activity", page)  # punctuation and case do not matter
    assert not dr.quote_is_on_page("at least 300 minutes of moderate intensity aerobic activity", page)
    assert not dr.quote_is_on_page("150 minutes", page)  # too short to prove anything


def test_made_up_claims_are_dropped_and_counted():
    report = run()
    texts = [c.text for g in report.groups for c in g["claims"]]
    assert "The guideline was approved by the moon" not in texts and report.dropped == 1
    assert any("150 minutes" in t for t in texts)


def test_sources_that_agree_are_well_supported_and_a_lone_site_is_not():
    groups = lambda user: [{"statement": "Adults need about 150 minutes of moderate activity a week", "ids": [1, 2], "conflict": False},
                           {"statement": "30 minutes a month is enough", "ids": [3], "conflict": False}]
    report = dr.deep_research("How much exercise do adults need?", search, fetch, model(claims=claims_by_page, groups=groups))
    by_statement = {g["statement"]: g for g in report.groups}
    agreed = by_statement["Adults need about 150 minutes of moderate activity a week"]
    lone = by_statement["30 minutes a month is enough"]
    assert agreed["status"] == "well supported" and agreed["best_tier"] == 4 and len(agreed["sites"]) == 2
    assert lone["status"] == "one source only" and lone["best_tier"] == 2
    assert report.groups[0] is agreed  # the best supported finding comes first


def test_contradicting_sources_are_flagged_not_averaged():
    groups = lambda user: [{"statement": "Sources disagree: 150 minutes a week vs 30 minutes a month", "ids": [1, 2, 3], "conflict": True}]
    report = dr.deep_research("q", search, fetch, model(claims=claims_by_page, groups=groups))
    assert report.groups[0]["status"] == "sources disagree"


def test_the_report_numbers_sources_for_the_answer_cards_and_says_what_was_checked():
    from voice_assistant.research import parse_sources

    text = dr.format_report(run(), today="Friday 1 January 2027")
    assert "Deep research for:" in text and "## How much exercise do adults need each week?" in text
    assert "[1] WHO | https://www.who.int/exercise | who.int | updated 2025-03-02 | official or peer-reviewed" in text
    assert {s["url"] for s in parse_sources(text)} >= {"https://www.who.int/exercise", "https://www.nih.gov/exercise"}
    assert "dropped because their quote was not on the page" in text and "information, not professional advice" in text


def test_a_model_that_cannot_plan_still_gets_the_question_researched():
    ask = model(plan="I cannot do JSON", claims=claims_by_page)
    report = dr.deep_research("How much exercise do adults need?", search, fetch, ask)
    assert report.subquestions == ["How much exercise do adults need?"] and report.groups
    assert any("one piece" in n for n in report.notes)


def test_when_the_model_ignores_the_format_passages_stand_in_and_say_so():
    def ask(system, user):
        return "Sure! Here you go." if not system.startswith("You plan") else json.dumps({"kind": "general", "subquestions": [{"q": "exercise", "queries": ["exercise"]}]})

    report = dr.deep_research("exercise", search, fetch, ask)
    assert report.unverified_fallback and report.groups
    assert "unchecked page passages" in dr.format_report(report)


def test_it_stops_asking_the_model_when_time_runs_out():
    ticks = iter(range(0, 10_000, 100))
    ask = model(claims=claims_by_page)
    report = dr.deep_research("How much exercise do adults need?", search, fetch, ask, clock=lambda: next(ticks), budget_s=250)
    assert report.unverified_fallback  # the clock jumped past the budget before the claims were asked for
    assert ask.calls.count("From the passages of") < 3


def test_nothing_found_is_reported_honestly():
    report = dr.deep_research("q", lambda q: [], fetch, model())
    assert not report.groups and "No search results" in " ".join(report.notes)
    assert "Nothing checkable was found" in dr.format_report(report)


def test_unreadable_pages_do_not_spoil_the_rest():
    def flaky(url):
        return ("", "", "timeout") if "nih" in url else fetch(url)

    report = dr.deep_research("How much exercise do adults need?", search, flaky, model(claims=claims_by_page))
    assert report.groups and any(s.error == "timeout" for s in report.sources)
    assert "could not be read: timeout" in dr.format_report(report)


def test_specialist_sources_are_searched_for_medical_questions():
    asked = []

    def spy_fetch(url):
        asked.append(url)
        if "eutils" in url:
            return json.dumps({"esearchresult": {"idlist": ["111"]}}), "application/json", ""
        if "en.wikipedia.org/w/api.php" in url:
            return json.dumps(["x", ["Exercise"], ["desc"], ["https://en.wikipedia.org/wiki/Exercise"]]), "application/json", ""
        return fetch(url)

    dr.deep_research("How much exercise do adults need?", lambda q: [], spy_fetch, model(claims=claims_by_page))
    assert any("eutils.ncbi.nlm.nih.gov" in u for u in asked) and any("pubmed.ncbi.nlm.nih.gov/111/" in u for u in asked)
    assert any("en.wikipedia.org/wiki/Exercise" in u for u in asked)


def test_arxiv_results_are_read_from_the_feed():
    feed = "<feed><entry><id>http://arxiv.org/abs/2401.00001v1</id><title> A   Paper </title><summary> About   things </summary></entry></feed>"
    found = dr.arxiv_search("things", lambda url: (feed, "application/atom+xml", ""))
    assert found == [{"title": "A Paper", "href": "http://arxiv.org/abs/2401.00001v1", "body": "About things"}]


def test_the_tool_runs_the_pipeline_with_the_models_voice(monkeypatch):
    class Event:
        def __init__(self, text):
            self.kind, self.text = "text", text

    class FakeLLM:
        def stream_chat(self, messages):
            system = messages[0]["content"]
            if system.startswith("You plan"):
                yield Event(json.dumps({"kind": "general", "subquestions": [{"q": "exercise", "queries": ["exercise"]}]}))
            else:
                yield Event("[]")

    monkeypatch.setattr("voice_assistant.agent_tools.fetch_raw", lambda url, timeout=10: fetch(url))
    monkeypatch.setitem(__import__("sys").modules, "ddgs", type("M", (), {"DDGS": lambda self=None: type("D", (), {"text": lambda s, q, max_results=6: search(q)})()}))
    (tool,) = dr.build_deep_research_tools(lambda: FakeLLM())
    assert tool.name == "deep_research"
    assert "Deep research for: exercise" in tool.handler("exercise")


def test_forums_and_social_sites_are_left_out_when_better_sources_exist():
    def with_forum(query):
        return [{"title": "Thread", "href": "https://www.reddit.com/r/fitness/x", "body": ""}] + search(query)

    report = dr.deep_research("How much exercise do adults need?", with_forum, fetch, model(claims=claims_by_page))
    assert all("reddit.com" not in s.url for s in report.sources)
    only_forum = dr.deep_research("q", lambda q: [{"title": "Thread", "href": "https://www.reddit.com/r/fitness/x", "body": ""}], fetch, model(claims=claims_by_page))
    assert [s.site for s in only_forum.sources] == ["reddit.com"]  # better than nothing, and rated as a weak source


def test_json_is_found_inside_prose_and_lists_are_not_cut_down_to_their_first_item():
    assert dr._json_from('Sure! ```json\n[{"a": 1}, {"a": 2}]\n``` Done.') == [{"a": 1}, {"a": 2}]
    assert dr._json_from('Here: {"kind": "x", "n": [1, 2]} thanks') == {"kind": "x", "n": [1, 2]}
    assert dr._json_from("no json here") is None and dr._json_from("") is None


def test_pubmed_style_dates_are_read():
    from voice_assistant.research import page_date

    assert page_date('<meta name="citation_publication_date" content="2023/05/12">') == "2023-05-12"


def test_one_organisation_is_one_voice():
    assert dr.organisation("https://www.cdc.gov/x") == dr.organisation("https://archive.cdc.gov/y") == "cdc.gov"
    assert dr.organisation("https://www.bbc.co.uk/news") == "bbc.co.uk" and dr.organisation("https://who.int/") == "who.int"


def test_two_pages_of_the_same_organisation_do_not_count_as_two_sources():
    pages = {"https://www.cdc.gov/a": PAGE_NIH, "https://archive.cdc.gov/b": PAGE_NIH}
    both = lambda q: [{"title": "A", "href": "https://www.cdc.gov/a", "body": ""}, {"title": "B", "href": "https://archive.cdc.gov/b", "body": ""}]
    claims = [{"claim": "Adults need 150 minutes of moderate exercise a week", "quote": "Adults need at least 150 minutes of moderate exercise per week"}]
    groups = lambda user: [{"statement": "150 minutes a week", "ids": [1, 2], "conflict": False}]
    report = dr.deep_research("q", both, lambda url: (pages[url], "text/html", ""), model(claims=claims, groups=groups))
    assert [s.site for s in report.sources] == ["cdc.gov"]  # the second page of the same organisation was not even read
    assert report.groups[0]["status"] == "one source only"


def test_a_page_can_answer_several_sub_questions_and_is_read_once():
    plan = {"kind": "general", "subquestions": [{"q": "How much aerobic exercise do adults need?", "queries": ["aerobic"]}, {"q": "Does strength training matter for adults?", "queries": ["strength"]}]}
    fetched = []

    def counting_fetch(url):
        fetched.append(url)
        return fetch(url)

    report = dr.deep_research("exercise", search, counting_fetch, model(plan=plan, claims=claims_by_page))
    assert len(fetched) == len(set(fetched)) <= 3  # each page once, though both sub-questions used them
    assert {g["sub"] for g in report.groups} == {0, 1}
    assert "## Does strength training matter for adults?" in dr.format_report(report)
