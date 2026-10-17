"""Writes THIRD_PARTY_NOTICES.md from the installed packages' own license metadata.
Run:  .venv\Scripts\python store\make_notices.py"""
from importlib import metadata
from pathlib import Path

PACKAGES = ["PySide6", "sherpa-onnx", "sounddevice", "numpy", "keyboard", "httpx", "sentencepiece", "psutil", "ddgs",
            "keyring", "mcp", "pypdf", "num2words", "pyinstaller"]
rows = []
for name in PACKAGES:
    try:
        md = metadata.metadata(name)
        lic = md.get("License-Expression") or md.get("License") or ""
        if not lic or len(lic) > 80:
            lic = next((c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")), "see package")
        rows.append((md["Name"], metadata.version(name), " ".join(lic.split())[:80], md.get("Home-page") or ""))
    except metadata.PackageNotFoundError:
        rows.append((name, "?", "not installed", ""))
text = ["# Third-party notices", "", "Generated from the installed packages' metadata. **Not legal advice; verify each licence before selling.**", "",
        "## Software bundled in the installer", "", "| Package | Version | Licence (as declared) |", "|---|---|---|"]
text += [f"| {n} | {v} | {l} |" for n, v, l, _ in rows]
text += ["", "## Downloaded on first run (not bundled in the installer)", "",
         "| Model | Source | Licence note |", "|---|---|---|",
         "| Streaming zipformer en 20M (speech recognition) | k2-fsa/sherpa-onnx releases | Apache-2.0 (its README) |",
         "| Parakeet TDT 0.6B v2 (accurate recognition) | NVIDIA via k2-fsa/sherpa-onnx releases | CC-BY-4.0: credit NVIDIA |",
         "| Kokoro English v0.19 (voices) | k2-fsa/sherpa-onnx releases | Apache-2.0 (LICENSE file ships with the model) |",
         "| Piper en_US amy low (fallback voice) | k2-fsa/sherpa-onnx releases | **unclear**: its MODEL_CARD only says 'See URL' (mimic3-voices dataset). Do not ship or advertise it until confirmed; Kokoro alone is enough |",
         "| Zipformer KWS (wake word) | k2-fsa/sherpa-onnx releases | Apache-2.0 per its README |",
         "", "## Needs a decision before selling", "",
         "- **espeak-ng** (GPL-3.0) supplies the phonemizer data used by the Kokoro and Piper voices through sherpa-onnx. Confirm whether the way it is linked and distributed is compatible with a closed-source paid app, or replace/separate it.",
         "- **PySide6 / Qt** is LGPL-3.0: keep it dynamically linked (the installer does) and include the LGPL text and a way to relink.",
         "- **num2words** is LGPL: it is bundled inside the packaged program, which makes 'replace this library' awkward. Either ship it as a separate folder in the installer, or write the small number-to-words routine yourself.",
         "- **PyInstaller** bootloader is GPL-2.0 with an exception that permits commercial use.",
         "- **ddgs** searches DuckDuckGo by scraping: check its terms or make web search opt-in only (it already is off by default).",
         "- **VB-Audio Virtual Cable** is donationware and must not be bundled or redistributed: link to it only."]
Path(__file__).resolve().parents[1].joinpath("THIRD_PARTY_NOTICES.md").write_text("\n".join(text) + "\n", encoding="utf-8")
print("\n".join(f"{n:14s} {v:10s} {l}" for n, v, l, _ in rows))
