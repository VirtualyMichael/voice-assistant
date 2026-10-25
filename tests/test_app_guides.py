from pathlib import Path

from voice_assistant.library import Library, bundled_folder

QUESTIONS = {
    "how do I record my screen in OBS": "OBS Studio",
    "my microphone is not picked up in Discord voice chat": "Discord",
    "reopen a closed tab": "Google Chrome",
    "VLOOKUP shows #N/A": "Microsoft Excel",
    "take a screenshot with win shift s": "Windows 11",
    "move a game to another drive": "Steam",
    "debug python breakpoint": "Visual Studio Code",
    "download songs for offline listening on YouTube Music": "YouTube Music",
}


def test_every_guide_has_the_standard_sections():
    folder = bundled_folder("app guides")
    assert folder is not None
    guides = sorted(folder.glob("*.md"))
    assert len(guides) >= 8
    for g in guides:
        text = g.read_text(encoding="utf-8")
        for heading in ("## Basics", "## Do this", "## When it goes wrong", "## Where to look next"):
            assert heading in text, f"{g.name} is missing {heading}"
        assert text.startswith("# ") and "Written for" in text.splitlines()[2], f"{g.name} should say which version it was written for"


def test_a_question_finds_the_guide_for_that_app(tmp_path):
    lib = Library(tmp_path / "lib.db")
    lib.sync([str(bundled_folder("app guides"))])
    assert [c for c, _n, _k in lib.collections()] == ["app guides"]
    for question, title in QUESTIONS.items():
        hits = lib.search(question, k=3)
        assert hits, question
        assert hits[0].title == title, f"{question!r} found {[h.title for h in hits]}"
