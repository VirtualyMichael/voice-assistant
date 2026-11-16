"""python -m voice_assistant audition [--out DIR] [--only pocket,elevenlabs]: one WAV per available voice plus an index.html to compare them by ear.

Every voice says the same three sentences. The page groups voices by engine and shows how long each took to produce the first sentence
(the wait before you would hear anything) and the whole set. Online engines are called for real, so ElevenLabs uses account credits.
"""
from __future__ import annotations

import html
import re
import time
import wave
from pathlib import Path

import numpy as np

SENTENCES = (
    "Sure, I can do that.",
    "Today will be mostly sunny with a high of twenty two degrees. Expect a light breeze from the west in the afternoon.",
    "Would you like me to set a reminder to take an umbrella tomorrow?",
)
ENGINE_NAMES = {"kokoro": "Kokoro", "supertonic": "Supertonic", "piper": "Piper", "chatterbox": "Chatterbox (cloned voice)", "pocket": "Pocket",
                "server": "Speech server", "elevenlabs": "ElevenLabs"}
GAP_S = 0.35


def flatten(synth) -> list:
    """The real engines behind the wrappers (MultiSynth, FallbackSynth, PendingClone), in voice-list order."""
    if "primary" in getattr(synth, "__dict__", {}):  # FallbackSynth: audition the engine itself, so a failure shows instead of the backup
        return flatten(synth.primary)
    if hasattr(synth, "engines"):
        return [e for inner in synth.engines for e in flatten(inner)]
    if hasattr(synth, "_builtin") and hasattr(synth, "ready"):  # PendingClone: the clone (once its server answers) and the built-in voices
        return ([synth._clone.primary] if synth._clone is not None else []) + flatten(synth._builtin)
    return [synth]


def write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    pcm = (np.clip(np.asarray(samples, np.float32), -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes(pcm.tobytes())


def _file_name(engine: str, voice_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{engine}-{voice_id}").strip("_") + ".wav"


def audition(synth, out: Path, sentences=SENTENCES, only: set[str] | None = None, clock=time.perf_counter, say=print) -> list[dict]:
    """Speak `sentences` with every voice of every engine into `out`; returns one row per voice (error rows too) and writes index.html."""
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for engine in flatten(synth):
        name = getattr(engine, "engine", "voice")
        if only and name not in only:
            continue
        if hasattr(engine, "wait_voices"):
            engine.wait_voices(20.0)
        for v in list(engine.voices):
            row = {"engine": name, "id": v.id, "label": v.label}
            try:
                engine.set_voice(v.id)
                if hasattr(engine, "load"):
                    engine.load()  # loading a model is not part of the time to the first sound
                parts, sr, start, first = [], 0, clock(), None
                for s in sentences:
                    samples, sr_s = engine(s)
                    if first is None:
                        first = clock() - start
                    if sr and sr_s != sr:
                        raise ValueError(f"sample rate changed from {sr} to {sr_s}")
                    sr = sr_s
                    parts += [np.asarray(samples, np.float32), np.zeros(int(sr * GAP_S), np.float32)]
                total = clock() - start
                audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
                row.update(file=_file_name(name, v.id), first_s=first or 0.0, total_s=total, audio_s=len(audio) / sr if sr else 0.0)
                write_wav(out / row["file"], audio, sr)
                say(f"{name:12} {v.label:40} first sound {row['first_s']:.2f} s")
            except Exception as e:
                row["error"] = f"{e.__class__.__name__}: {e}"[:300]
                say(f"{name:12} {v.label:40} FAILED: {row['error']}")
            rows.append(row)
    (out / "index.html").write_text(index_html(rows, sentences), encoding="utf-8")
    return rows


def index_html(rows: list[dict], sentences=SENTENCES) -> str:
    esc = html.escape
    body, engines = [], list(dict.fromkeys(r["engine"] for r in rows))
    for eng in engines:
        body.append(f"<h2>{esc(ENGINE_NAMES.get(eng, eng))}</h2><table><tr><th>Voice</th><th>Listen</th><th>First sound</th><th>All three</th></tr>")
        for r in (r for r in rows if r["engine"] == eng):
            if "error" in r:
                body.append(f"<tr><td>{esc(r['label'])}</td><td colspan=3 class=err>Failed: {esc(r['error'])}</td></tr>")
                continue
            body.append(f"<tr><td>{esc(r['label'])}<br><small>{esc(r['id'])}</small></td><td><audio controls preload=none src=\"{esc(r['file'])}\"></audio></td>"
                        f"<td>{r['first_s']:.2f} s</td><td>{r['total_s']:.2f} s for {r['audio_s']:.1f} s of speech</td></tr>")
        body.append("</table>")
    said = "".join(f"<li>{esc(s)}</li>" for s in sentences)
    return ("<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width, initial-scale=1'><title>Voice audition</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:16px;max-width:1000px;background:#fff;color:#111}table{border-collapse:collapse;width:100%}"
            "td,th{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:middle}audio{width:260px;max-width:100%}.err{color:#b00}"
            "@media (prefers-color-scheme:dark){body{background:#111;color:#eee}td,th{border-color:#333}}</style></head><body>"
            f"<h1>Voice audition</h1><p>Each voice says:</p><ol>{said}</ol>"
            "<p><b>First sound</b> is how long the first sentence took to make: roughly the wait before you hear a reply start. "
            "Model loading is not counted. Network voices also depend on your connection.</p>"
            + "".join(body) + ("" if rows else "<p>No voices are available.</p>") + "</body></html>")


def main(args: list[str]) -> int:
    from .config import config_dir, load_config
    from .models import PendingClone, load_synth
    from .tts_engines import voice_engines

    out = Path(args[args.index("--out") + 1]) if "--out" in args and args.index("--out") + 1 < len(args) else Path("voice-samples/audition")
    only = set(args[args.index("--only") + 1].split(",")) if "--only" in args and args.index("--only") + 1 < len(args) else None
    cfg = load_config()
    models = Path(cfg.models_dir)
    synth = load_synth(models, cfg.tts_speed, cfg.tts_threads, cfg.tts_voice, cfg.custom_voice_dir, cfg.chatterbox_url, cfg.chatterbox_launch,
                       extra=voice_engines(cfg, models, config_dir() / "voices"))
    clones = [e for e in getattr(synth, "engines", [synth]) if isinstance(e, PendingClone)]
    if clones:
        print("Waiting up to 3 minutes for the cloned voice server...")
        deadline = time.time() + 180
        while not clones[0].ready and time.time() < deadline:
            time.sleep(1)
    rows = audition(synth, out, only=only)
    ok = sum("error" not in r for r in rows)
    print(f"{ok} of {len(rows)} voices written. Open {(out / 'index.html').resolve()}")
    return 0 if ok else 1
