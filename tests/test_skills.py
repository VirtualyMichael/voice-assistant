import json

import pytest

from voice_assistant.mcp_client import read_mcp_json
from voice_assistant.pdf_tools import PdfStore, parse_pages
from voice_assistant.permissions import FolderPolicy
from voice_assistant.skill_tools import build_skill_tools
from voice_assistant.skills import SkillLibrary, discover, parse_frontmatter
from voice_assistant.tools import ToolRegistry


def make_pdf(path, pages):
    """A minimal valid PDF whose pages carry the given text (no extra libraries needed)."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    font_obj = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + 2 * i} 0 R /Resources << /Font << /F1 {font_obj} 0 R >> >> >>")
        stream = f"BT /F1 12 Tf 50 700 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for n, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    path.write_bytes(out)


@pytest.fixture
def skills_root(tmp_path):
    mech = tmp_path / "skills" / "garage-mechanic"
    (mech / "references").mkdir(parents=True)
    (mech / "SKILL.md").write_text(
        "---\nname: garage-mechanic\ndescription: >\n  Maintenance help for a Honda CRF250R\n  and a Mazda CX-5.\n---\n\n# Garage Mechanic\nAlways cite the manual page.\n",
        encoding="utf-8",
    )
    make_pdf(mech / "references" / "manual.pdf", ["Introduction and safety", "Engine oil capacity is 0.9 liters of 10W-40", "Spark plug gap 0.7 mm", "Tire pressure 12 psi"])
    (mech / "references" / "notes.txt").write_text("torque values here", encoding="utf-8")
    quiet = tmp_path / "skills" / "hidden-helper"
    quiet.mkdir()
    (quiet / "SKILL.md").write_text("---\nname: hidden-helper\ndescription: internal\ndisable-model-invocation: true\n---\nbody", encoding="utf-8")
    return tmp_path / "skills"


def test_frontmatter_folded_quoted_and_plain_values():
    fields, body = parse_frontmatter("---\nname: x\ndisplay-name: 'My X'\ndescription: >\n  line one\n  line two\n---\nHello")
    assert fields == {"name": "x", "display-name": "My X", "description": "line one line two"} and body == "Hello"
    assert parse_frontmatter("no front matter")[0] == {}


def test_discovery_handles_single_skill_parent_and_skills_subfolder(skills_root, tmp_path):
    assert {s.name for s in discover([str(skills_root)])[0]} == {"garage-mechanic", "hidden-helper"}  # parent of skills
    assert [s.name for s in discover([str(skills_root / "garage-mechanic")])[0]] == ["garage-mechanic"]  # one skill
    assert {s.name for s in discover([str(skills_root.parent)])[0]} == {"garage-mechanic", "hidden-helper"}  # has a skills/ subfolder
    skills, problems = discover([str(tmp_path / "nope")])
    assert skills == [] and "not found" in problems[0]


def test_library_prompt_matching_and_disabling(skills_root):
    lib = SkillLibrary([str(skills_root)])
    assert "garage-mechanic" in lib.prompt() and "hidden-helper" not in lib.prompt()  # disable-model-invocation: not advertised
    assert lib.find("mechanic").name == "garage-mechanic" and lib.find("garage mechanic").name == "garage-mechanic"
    assert lib.find("hidden-helper").name == "hidden-helper"  # still usable when asked for by name
    lib.disabled = {"garage-mechanic"}
    assert lib.prompt() == "" and lib.find("garage-mechanic") is None


def registry(skills_root, tmp_path):
    lib = SkillLibrary([str(skills_root)])
    return ToolRegistry(build_skill_tools(lib, PdfStore(tmp_path / "cache"), FolderPolicy([tmp_path])))


def call(reg, tool, **a):
    return reg.call(tool, json.dumps(a))


def test_use_skill_returns_instructions_and_file_list(skills_root, tmp_path):
    reg = registry(skills_root, tmp_path)
    out = call(reg, "use_skill", name="mechanic")
    assert "Always cite the manual page." in out and "references/manual.pdf" in out
    assert "No skill called" in call(reg, "use_skill", name="astrology")


def test_skill_files_cannot_escape_the_skill_folder(skills_root, tmp_path):
    (tmp_path / "secret.txt").write_text("secret")
    reg = registry(skills_root, tmp_path)
    assert "torque values" in call(reg, "read_skill_file", skill="garage-mechanic", path="references/notes.txt")
    for bad in ("../../secret.txt", str(tmp_path / "secret.txt"), "references/notes.txt:stream"):
        assert "permission denied" in call(reg, "read_skill_file", skill="garage-mechanic", path=bad).lower()


def test_pdf_read_pages_and_search(skills_root, tmp_path):
    reg = registry(skills_root, tmp_path)
    out = call(reg, "read_skill_pdf", skill="garage-mechanic", path="references/manual.pdf", pages="2-3")
    assert "PDF page 2 of 4" in out and "Engine oil capacity" in out and "Spark plug gap" in out and "Tire pressure" not in out
    out = call(reg, "search_skill_pdf", skill="garage-mechanic", path="references/manual.pdf", query="how much engine oil")
    assert out.splitlines()[1].startswith("PDF page 2") and "0.9 liters" in out  # the right page ranks first
    assert "No page" in call(reg, "search_skill_pdf", skill="garage-mechanic", path="references/manual.pdf", query="xylophone")
    assert "none of" in call(reg, "read_skill_pdf", skill="garage-mechanic", path="references/manual.pdf", pages="40")
    assert "bad page list" in call(reg, "read_skill_pdf", skill="garage-mechanic", path="references/manual.pdf", pages="abc")


def test_pdf_in_approved_folder_and_cache_on_disk(skills_root, tmp_path):
    reg = registry(skills_root, tmp_path)
    pdf = str(skills_root / "garage-mechanic" / "references" / "manual.pdf")
    assert "Spark plug gap" in call(reg, "search_pdf", path=pdf, query="spark plug")
    assert any((tmp_path / "cache").glob("*.json"))  # extracted text is cached for next time
    assert "permission denied" in call(reg, "read_pdf", path=str(tmp_path.parent / "x.pdf"), pages="1").lower()


def test_page_spec_parsing():
    assert parse_pages("5", 10) == [5] and parse_pages("2-4, 9", 10) == [2, 3, 4, 9] and parse_pages("8-30", 10) == [8, 9, 10]
    with pytest.raises(ValueError):
        parse_pages("1;2", 10)


def test_import_mcp_json_keeps_local_servers_and_reports_remote(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {"science": {"command": "python", "args": ["s.py"]}, "remote": {"url": "https://x"}, "k": {"command": "npx", "env": {"A": "1"}}}}))
    servers, skipped = read_mcp_json(f)
    assert servers == {"science": {"command": "python", "args": ["s.py"]}, "k": {"command": "npx", "env": {"A": "1"}}} and skipped == ["remote"]
