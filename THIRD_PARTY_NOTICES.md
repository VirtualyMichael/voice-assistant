# Third-party notices

Generated from the installed packages' metadata. **Not legal advice; verify each licence before selling.**

## Software bundled in the installer

| Package | Version | Licence (as declared) |
|---|---|---|
| PySide6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only |
| sherpa-onnx | 1.13.8 | Apache licensed, as found in the LICENSE file |
| sounddevice | 0.5.6 | MIT |
| numpy | 2.4.6 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| keyboard | 0.13.5 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| sentencepiece | 0.2.2 | Apache-2.0 |
| psutil | 7.2.2 | BSD-3-Clause |
| ddgs | 9.16.0 | MIT |
| keyring | 25.7.0 | MIT |
| mcp | 2.3.0 | MIT |
| pypdf | 6.21.1 | BSD-3-Clause |
| num2words | 0.5.14 | LGPL |
| pyinstaller | 6.22.3 | GNU General Public License v2 (GPLv2) |

## Downloaded on first run (not bundled in the installer)

| Model | Source | Licence note |
|---|---|---|
| Streaming zipformer en 20M (speech recognition) | k2-fsa/sherpa-onnx releases | Apache-2.0 (its README) |
| Parakeet TDT 0.6B v2 (accurate recognition) | NVIDIA via k2-fsa/sherpa-onnx releases | CC-BY-4.0: credit NVIDIA |
| Kokoro English v0.19 (voices) | k2-fsa/sherpa-onnx releases | Apache-2.0 (LICENSE file ships with the model) |
| Piper en_US amy low (fallback voice) | k2-fsa/sherpa-onnx releases | **unclear**: its MODEL_CARD only says 'See URL' (mimic3-voices dataset). Do not ship or advertise it until confirmed; Kokoro alone is enough |
| Zipformer KWS (wake word) | k2-fsa/sherpa-onnx releases | Apache-2.0 per its README |

## Phone app only

| Item | Source | Licence note |
|---|---|---|
| LiteRT-LM (`com.google.ai.edge.litertlm`) 0.16.1, runs the offline model | Google AI Edge | Apache-2.0 |
| Gemma 4 E2B (offline helper model), downloaded by the user inside the app, not bundled | litert-community/gemma-4-E2B-it-litert-lm on Hugging Face | **confirm the Gemma terms on its model card (use and redistribution rules) before release** |

## Needs a decision before selling

- **espeak-ng** (GPL-3.0) supplies the phonemizer data used by the Kokoro and Piper voices through sherpa-onnx. Confirm whether the way it is linked and distributed is compatible with a closed-source paid app, or replace/separate it.
- **PySide6 / Qt** is LGPL-3.0: keep it dynamically linked (the installer does) and include the LGPL text and a way to relink.
- **num2words** is LGPL: it is bundled inside the packaged program, which makes 'replace this library' awkward. Either ship it as a separate folder in the installer, or write the small number-to-words routine yourself.
- **PyInstaller** bootloader is GPL-2.0 with an exception that permits commercial use.
- **ddgs** searches DuckDuckGo by scraping: check its terms or make web search opt-in only (it already is off by default).
- **VB-Audio Virtual Cable** is donationware and must not be bundled or redistributed: link to it only.
