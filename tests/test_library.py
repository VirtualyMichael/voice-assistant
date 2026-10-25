"""The local reference library: index folders of documents, find the best passages across all of them, forget what is removed."""
import os
import time

import pytest

from voice_assistant import library as lib


@pytest.fixture
def folders(tmp_path):
    med = tmp_path / "medicine"
    med.mkdir()
    (med / "hypertension-guideline.md").write_text(
        "# Hypertension\n\nBlood pressure above 130/80 is stage 1 hypertension in adults. Lifestyle changes come first: less salt, more exercise.\n\n"
        "# Treatment\n\nFirst line drugs include thiazide diuretics, ACE inhibitors and calcium channel blockers.\n", encoding="utf-8")
    (med / "diabetes_notes.txt").write_text("Type 2 diabetes is managed with diet, exercise and metformin as the usual first medicine. HbA1c below 7 percent is a common target.", encoding="utf-8")
    code = tmp_path / "python"
    code.mkdir()
    (code / "lists.html").write_text("<html><body><h1>Lists</h1><p>Use list.append(x) to add one item to the end of a list. Use extend to add many items at once.</p></body></html>", encoding="utf-8")
    return tmp_path, med, code


@pytest.fixture
def library(tmp_path, folders):
    L = lib.Library(tmp_path / "library.db")
    yield L
    L.close()


def test_passages_stay_under_the_size_limit_and_overlap_without_duplicating():
    text = "\n\n".join(" ".join(f"Fact {i}-{j} about the heart is stated plainly here." for j in range(12)) for i in range(6))
    chunks = lib.chunk_text(text)
    assert len(chunks) >= 5 and max(len(c) for c in chunks) <= lib.CHUNK_CHARS
    assert len(chunks) == len(set(chunks))
    assert [len(c) for c in lib.chunk_text("x" * 2500)] == [900, 900, 900, 250]


def test_queries_become_or_searches_without_filler_words():
    assert lib.match_query("What is the first line treatment for hypertension?") == '"first" OR "line" OR "treatment" OR "hypertension"'
    assert lib.match_query("the of a") == "" and lib.match_query("") == ""
    assert 'OR' in lib.match_query("C++ list.append") and "drop table" not in lib.match_query('x"; drop table docs; --')


def test_a_folder_is_indexed_and_questions_find_the_right_document(library, folders):
    _root, med, code = folders
    assert library.add_folder(med) == (2, 0) and library.add_folder(code) == (1, 0)
    hit = library.search("first line drugs for high blood pressure")[0]
    assert hit.title == "hypertension guideline" and hit.collection == "medicine" and hit.where == "§ Treatment" and "thiazide" in hit.text
    assert library.search("how do I add an item to a list")[0].collection == "python"
    assert library.search("metformin")[0].title == "diabetes notes"


def test_a_search_can_be_limited_to_one_collection(library, folders):
    _root, med, code = folders
    library.add_folder(med), library.add_folder(code)
    assert library.search("add items", collection="medicine") == []
    assert library.search("add items", collection="Python")  # the name does not need the same case
    assert {c for c, _n, _k in library.collections()} == {"medicine", "python"}


def test_unchanged_files_are_not_read_again_but_edited_ones_are(library, folders):
    _root, med, _code = folders
    library.add_folder(med)
    assert library.add_folder(med) == (0, 2)
    path = med / "diabetes_notes.txt"
    path.write_text("Insulin is used when metformin is not enough to control blood sugar.", encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert library.add_folder(med) == (1, 1)
    assert library.search("insulin")[0].title == "diabetes notes" and library.search("HbA1c") == []  # the old text is gone from the index


def test_removing_a_folder_or_a_file_removes_its_knowledge(library, folders):
    _root, med, code = folders
    library.sync([str(med), str(code)])
    assert library.search("metformin")
    (med / "diabetes_notes.txt").unlink()
    library.sync([str(med), str(code)])
    assert library.search("metformin") == [] and library.search("thiazide")
    library.sync([str(code)])  # the medicine folder is no longer part of the library
    assert library.search("thiazide") == [] and library.search("append")


def test_missing_folders_and_unreadable_files_are_reported_not_fatal(library, tmp_path, folders):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "broken.pdf").write_bytes(b"this is not a pdf")
    (bad / "ok.txt").write_text("Plenty of readable words about orbits and planets here today.", encoding="utf-8")
    library.sync([str(bad), str(tmp_path / "gone")])
    assert any("broken.pdf" in p for p in library.problems) and any("gone" in p and "not found" in p for p in library.problems)
    assert library.search("planets")


def test_a_pdf_page_is_cited_by_number(library, tmp_path, monkeypatch):
    pdf = tmp_path / "docs" / "manual.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(lib, "extract", lambda path: [("p. 1", "Introduction to the machine and its parts in plain words."), ("p. 2", "Torque the caliper bolt to twenty five newton metres every time.")])
    library.add_folder(pdf.parent)
    hit = library.search("caliper bolt torque")[0]
    assert hit.where == "p. 2" and hit.title == "manual"


def test_the_tools_answer_with_numbered_passages_and_say_when_nothing_matches(library, folders):
    _root, med, _code = folders
    library.add_folder(med)
    search, listing = lib.build_library_tools(lambda: library)
    text = search.handler("first line drugs for hypertension")
    assert "[1] hypertension guideline (medicine, § Treatment; file dated" in text and "thiazide" in text and "Check the file date" in text
    assert "Nothing in the reference library matches" in search.handler("quantum chromodynamics lattice")
    assert "medicine (2 documents)" in listing.handler()
    empty = lib.build_library_tools(lambda: None)
    assert "not set up" in empty[0].handler("x") and "empty" in empty[1].handler()


def test_search_survives_odd_input(library, folders):
    library.add_folder(folders[1])
    for q in ['"', "AND OR NOT", "(", "*", "a" * 5000, "'; DROP TABLE docs; --"]:
        assert isinstance(library.search(q), list)
    assert library.collections()
