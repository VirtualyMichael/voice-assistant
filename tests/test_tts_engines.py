"""The more lifelike voice engines, with fakes only: no models, no keys, no network."""
import io
import json
import threading
import wave
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

import voice_assistant.models as M
import voice_assistant.tts_engines as T
from voice_assistant import audition as AU
from voice_assistant.config import Config


def wav_bytes(samples, sr=16000, width=2, channels=1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def pcm16(values) -> bytes:
    return np.asarray(values, "<i2").tobytes()


# ---- decoding ----
def test_raw_pcm_reply_is_24k_16_bit_mono():
    x, sr = T.decode_audio(pcm16([0, 16384, -32768]) + b"\x01")  # a stray odd byte is dropped
    assert sr == 24000 and x.dtype == np.float32 and np.allclose(x, [0.0, 0.5, -1.0])


def test_wav_reply_is_parsed_from_its_header():
    data = wav_bytes(np.array([0, 8192, -8192], "<i2"), sr=22050)
    x, sr = T.decode_audio(data)
    assert sr == 22050 and np.allclose(x, [0.0, 0.25, -0.25])


def test_streamed_wav_with_unknown_length_reads_to_the_end_and_stereo_is_mixed():
    data = bytearray(wav_bytes(np.array([1000, 3000, -2000, -4000], "<i2"), sr=8000, channels=2))
    at = data.index(b"data")
    data[at + 4:at + 8] = (0xFFFFFFFF).to_bytes(4, "little")
    x, sr = T.parse_wav(bytes(data))
    assert sr == 8000 and np.allclose(x * 32768, [2000, -3000])


def test_float_and_24_bit_wavs():
    fmt = lambda tag, bits: b"fmt " + (16).to_bytes(4, "little") + tag.to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little") + b"\0" * 6 + bits.to_bytes(2, "little")  # noqa: E731
    riff = lambda body: b"RIFF" + len(body).to_bytes(4, "little") + b"WAVE" + body  # noqa: E731
    f32 = np.array([0.5, -0.25], "<f4").tobytes()
    assert np.allclose(T.parse_wav(riff(fmt(3, 32) + b"data" + len(f32).to_bytes(4, "little") + f32))[0], [0.5, -0.25])
    s24 = bytes([0, 0, 0x40, 0, 0, 0xC0])  # +0.5 and -0.5
    assert np.allclose(T.parse_wav(riff(fmt(1, 24) + b"data" + (6).to_bytes(4, "little") + s24))[0], [0.5, -0.5])
    with pytest.raises(ValueError):
        T.parse_wav(b"not a wav file at all")


# ---- keys ----
def test_key_lookup_order():
    saved = {}
    get = lambda name, env="": saved.get(name, "") or ({"OPENAI_API_KEY": "sk-env"}.get(env, ""))  # noqa: E731
    assert T.server_key("https://api.openai.com/v1", get=get) == "sk-env"  # nothing saved: the chat's OpenAI key (here from its environment variable)
    saved["openai"] = "sk-chat"
    assert T.server_key("https://api.openai.com/v1", get=get) == "sk-chat"
    saved["tts_server"] = "sk-speech"
    assert T.server_key("https://api.openai.com/v1", get=get) == "sk-speech"  # its own key wins
    del saved["tts_server"]
    assert T.server_key("http://127.0.0.1:8880/v1", get=get) == ""  # a local server never gets the OpenAI key
    assert T.eleven_key(get=lambda name, env="": f"{name}|{env}") == "elevenlabs|ELEVENLABS_API_KEY"


def test_engines_are_absent_until_set_up(tmp_path):
    cfg = Config(models_dir=str(tmp_path))
    none = lambda name, env="": ""  # noqa: E731
    assert T.voice_engines(cfg, tmp_path, tmp_path / "voices", get=none) == []
    cfg.tts_server_url = "https://api.openai.com/v1"
    assert T.voice_engines(cfg, tmp_path, None, get=none) == []  # OpenAI without a key: absent
    cfg.tts_server_url = "http://127.0.0.1:8880/v1"
    (srv,) = T.voice_engines(cfg, tmp_path, None, get=none)
    assert srv.engine == "server" and srv.voices[0].id == "server:alloy"
    (tmp_path / "pocket").mkdir()  # Pocket folder without any recording: still absent
    assert [e.engine for e in T.voice_engines(cfg, tmp_path, None, get=none)] == ["server"]


# ---- speech server ----
def server(handler, **kw):
    return T.SpeechServerSynth(kw.pop("url", "http://127.0.0.1:8880/v1"), kw.pop("model", "kokoro"), kw.pop("voices", "af_heart, am_echo"),
                               client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_speech_server_request_and_pcm_reply():
    seen = []

    def handler(req):
        seen.append((str(req.url), dict(req.headers), json.loads(req.content)))
        return httpx.Response(200, content=pcm16([0, 16384]))

    s = server(handler, key="sk-1", instructions="Warm and calm")
    assert s.set_voice("server:am_echo").label == "am_echo (Speech server)"
    x, sr = s("Hello there.")
    url, headers, body = seen[0]
    assert url == "http://127.0.0.1:8880/v1/audio/speech" and headers["authorization"] == "Bearer sk-1"
    assert body == {"model": "kokoro", "input": "Hello there.", "voice": "am_echo", "response_format": "pcm", "instructions": "Warm and calm"}
    assert sr == 24000 and np.allclose(x, [0, 0.5])


def test_speech_server_sends_no_style_or_key_when_empty_and_reads_wav():
    seen = []

    def handler(req):
        seen.append((dict(req.headers), json.loads(req.content)))
        return httpx.Response(200, content=wav_bytes(np.array([8192], "<i2"), sr=44100))

    s = server(handler)
    s.speed = 1.25
    x, sr = s("Hi")
    headers, body = seen[0]
    assert "authorization" not in headers and "instructions" not in body and body["speed"] == 1.25 and body["voice"] == "af_heart"
    assert sr == 44100 and np.allclose(x, [0.25])


def test_speech_server_error_raises():
    s = server(lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        s("Hi")


# ---- ElevenLabs ----
def test_elevenlabs_lists_voices_and_streams_pcm():
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path == "/v1/voices":
            return httpx.Response(200, json={"voices": [{"voice_id": "abc", "name": "Rachel"}, {"voice_id": "def", "name": "Adam"}, {"name": "broken"}]})
        return httpx.Response(200, content=pcm16([16384, -16384]))

    changed = threading.Event()
    e = T.ElevenLabsSynth("xi-key", client=httpx.Client(transport=httpx.MockTransport(handler)), on_voices=changed.set)
    assert e.wait_voices(5) and changed.is_set()
    assert [(v.id, v.label) for v in e.voices] == [("eleven:abc", "Rachel (ElevenLabs)"), ("eleven:def", "Adam (ElevenLabs)")]
    e.set_voice("eleven:def")
    x, sr = e("Hello")
    req = calls[-1]
    assert req.url.path == "/v1/text-to-speech/def/stream" and req.url.params["output_format"] == "pcm_24000"
    assert req.headers["xi-api-key"] == "xi-key" and json.loads(req.content) == {"text": "Hello", "model_id": "eleven_flash_v2_5"}
    assert sr == 24000 and np.allclose(x, [0.5, -0.5])


def test_elevenlabs_list_failure_means_no_voices_and_a_warning(caplog):
    e = T.ElevenLabsSynth("bad", client=httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(401))), fetch=False)
    e.fetch_voices()
    assert e.voices == [] and "Could not list your ElevenLabs voices" in caplog.text and "bad" not in caplog.text
    with pytest.raises(M.ModelsMissing):
        e("Hello")


# ---- Pocket ----
class FakePocket:
    def __init__(self):
        self.calls = []

    def generate(self, text, g):
        self.calls.append((text, len(g.reference_audio), g.reference_sample_rate, g.speed))
        return SimpleNamespace(samples=[0.1, 0.2], sample_rate=24000)


def test_pocket_voices_come_from_bundled_and_added_recordings(tmp_path):
    model, mine = tmp_path / "pocket", tmp_path / "voices"
    (model / "test_wavs").mkdir(parents=True)
    (model / "test_wavs" / "bria.wav").write_bytes(wav_bytes(np.zeros(16000, "<i2")))
    src = tmp_path / "My Voice!.wav"
    src.write_bytes(wav_bytes(np.full(32000, 1000, "<i2")))
    assert T.add_voice_file(src, mine).name == "My Voice.wav"
    assert T.add_voice_file(src, mine).name == "My Voice-2.wav"  # never overwrites
    short = tmp_path / "short.wav"
    short.write_bytes(wav_bytes(np.zeros(100, "<i2")))
    with pytest.raises(ValueError):
        T.add_voice_file(short, mine)
    fake = FakePocket()
    p = T.PocketSynth(model, mine, speed=1.1, make=lambda folder, threads: fake)
    assert [(v.id, v.label) for v in p.voices] == [("pocket:bria", "bria (Pocket)"), ("pocket:My Voice", "My Voice (Pocket)"), ("pocket:My Voice-2", "My Voice 2 (Pocket)")]
    p.set_voice("pocket:My Voice")
    x, sr = p("Hello")
    assert fake.calls == [("Hello", 32000, 16000, pytest.approx(1.1))] and sr == 24000 and x.dtype == np.float32
    (mine / "new.wav").write_bytes(wav_bytes(np.zeros(16000, "<i2")))
    p.refresh_voices()
    assert "pocket:new" in [v.id for v in p.voices]


def test_pocket_model_files_are_found_by_their_config_names(tmp_path):
    for name in ("lm_flow.int8.onnx", "lm_flow.onnx", "lm_main.onnx", "encoder.onnx", "decoder.onnx", "text_conditioner.onnx", "vocab.json", "token_scores.json"):
        (tmp_path / name).write_text("x")
    assert T._pick_part(tmp_path, "lm_flow", ".onnx").endswith("lm_flow.onnx")  # an exact name wins
    assert T._pick_part(tmp_path, "vocab", ".json").endswith("vocab.json")
    with pytest.raises(M.ModelsMissing):
        T._pick_part(tmp_path, "missing", ".onnx")


def test_pocket_is_skipped_when_its_folder_is_absent(tmp_path):
    cfg = Config(models_dir=str(tmp_path))
    (tmp_path / "voices").mkdir()
    (tmp_path / "voices" / "me.wav").write_bytes(wav_bytes(np.zeros(16000, "<i2")))
    assert T.voice_engines(cfg, tmp_path, tmp_path / "voices", get=lambda n, e="": "") == []


# ---- fallback ----
class Eng:
    def __init__(self, name, ids, fail=False, prefix=""):
        self.engine, self.prefix, self.speed, self.fail, self.calls = name, prefix, 1.0, fail, 0
        self.voices = [M.Voice(i, i, "other", n) for n, i in enumerate(ids)]

    def set_voice(self, vid):
        return next((v for v in self.voices if v.id == vid), M.Voice(vid, vid, "other", 0))

    def __call__(self, text):
        self.calls += 1
        if self.fail:
            raise httpx.ConnectTimeout("down")
        return np.array([1.0], np.float32), 24000


def test_a_failing_engine_falls_back_per_sentence_then_pauses_for_a_minute(caplog):
    now = [0.0]
    primary, builtin = Eng("server", ["server:alloy"], fail=True), Eng("kokoro", ["af"])
    f = T.FallbackSynth(primary, lambda text: (np.array([0.5], np.float32), 16000), clock=lambda: now[0])
    for _ in range(3):
        assert f("Rozumiem.")[1] == 16000  # never silent
    assert primary.calls == 3 and caplog.text.count("failed") == 3 and "Rozumiem" not in caplog.text
    now[0] = 59.0
    f("next")
    assert primary.calls == 3  # paused: no timeout per sentence
    now[0] = 61.0
    primary.fail = False
    assert f("back")[1] == 24000 and primary.calls == 4 and f.failures == 0
    assert f.engine == "server" and f.voices[0].id == "server:alloy"
    f.speed = 1.3
    assert primary.speed == 1.3


def test_one_success_resets_the_failure_count():
    primary = Eng("server", ["s"], fail=True)
    f = T.FallbackSynth(primary, lambda t: (np.zeros(1, np.float32), 16000), clock=lambda: 0.0)
    f("a"), f("b")
    primary.fail = False
    f("c")
    primary.fail = True
    f("d"), f("e")
    assert f.paused_until == 0.0 and f.failures == 2


def test_without_a_backup_the_error_still_surfaces():
    with pytest.raises(httpx.ConnectTimeout):
        T.FallbackSynth(Eng("server", ["s"], fail=True), None)("hi")


def test_cloned_voice_falls_back_once_ready():
    class Clone(Eng):
        pass

    clone = Clone("chatterbox", ["clone"], fail=True)
    builtin = Eng("kokoro", ["af"])
    ready = threading.Event()
    synth = M.PendingClone(builtin, "http://x", "", 1.0, on_ready=ready.set, ensure=lambda u, l: None, make=lambda u, s: clone)
    assert ready.wait(5)
    assert synth.ready and synth.engine == "chatterbox" and synth("Polish text")[1] == 24000 and clone.calls == 1 and builtin.calls == 1


# ---- routing in the one voice list ----
def test_multisynth_routes_prefixed_ids_and_lists_late_voices():
    builtin = Eng("kokoro", ["af_heart", "am_echo"])
    eleven = Eng("elevenlabs", [], prefix="eleven:")
    pocket = Eng("pocket", ["pocket:me"], prefix="pocket:")
    m = M.MultiSynth([builtin, T.FallbackSynth(pocket, builtin), T.FallbackSynth(eleven, builtin)])
    assert m.set_voice("eleven:xyz").id == "eleven:xyz" and m.engine_for("eleven:xyz").engine == "elevenlabs"  # chosen before its list arrived
    eleven.voices.append(M.Voice("eleven:xyz", "Rachel (ElevenLabs)", "other", 0))
    assert [v.id for v in m.voices] == ["af_heart", "am_echo", "pocket:me", "eleven:xyz"]
    m.set_voice("pocket:me")
    m("hi")
    assert pocket.calls == 1 and builtin.calls == 0
    assert m.set_voice("unknown").id == "unknown" and m("hi") and builtin.calls == 1  # an unknown id goes to the first engine


def test_load_synth_puts_the_extra_engines_behind_the_built_in_voice(monkeypatch, tmp_path):
    builtin = Eng("kokoro", ["af"])
    monkeypatch.setattr(M, "_local_synth", lambda *a, **k: builtin)
    server_eng = Eng("server", ["server:alloy"], fail=True, prefix="server:")
    synth = M.load_synth(tmp_path, 1.0, 2, "server:alloy", extra=[server_eng])
    assert isinstance(synth, M.MultiSynth) and [v.id for v in synth.voices] == ["af", "server:alloy"]
    assert synth("hi")[1] == 24000 and server_eng.calls == 1 and builtin.calls == 1
    assert M.load_synth(tmp_path, 1.0, 2, "af") is builtin  # no extra engines: unchanged


def test_extra_engines_still_work_without_any_built_in_voice(monkeypatch, tmp_path):
    def missing(*a, **k):
        raise M.ModelsMissing("no models")

    monkeypatch.setattr(M, "_local_synth", missing)
    synth = M.load_synth(tmp_path, 1.0, 2, "", extra=[Eng("server", ["server:alloy"], prefix="server:")])
    assert synth("hi")[1] == 24000
    with pytest.raises(M.ModelsMissing):
        M.load_synth(tmp_path, 1.0, 2, "")


def test_new_engine_audio_reaches_the_phone_in_the_same_format():
    from voice_assistant.remote import to_pcm16

    s = server(lambda req: httpx.Response(200, content=pcm16([16384, -16384])))
    samples, sr = s("Hi")
    assert isinstance(sr, int) and samples.ndim == 1 and to_pcm16(samples).tolist() == [16383, -16383]


def test_model_is_installed_without_tokens_file(tmp_path):
    (tmp_path / "pocket").mkdir()
    assert not M.is_installed(tmp_path, "pocket")
    (tmp_path / "pocket" / "lm_main.onnx").write_text("x")
    assert M.is_installed(tmp_path, "pocket")
    spec = next(s for s in M.MODEL_SPECS if s.slot == "pocket")
    assert not spec.core and spec.url.endswith("tts-models/sherpa-onnx-pocket-tts-2026-01-26.tar.bz2") and spec.size_mb == 160


# ---- audition ----
def test_audition_writes_one_wav_per_voice_and_an_index(tmp_path):
    builtin = Eng("kokoro", ["af_heart"])
    broken = Eng("server", ["server:alloy"], fail=True, prefix="server:")
    synth = M.MultiSynth([builtin, T.FallbackSynth(broken, builtin)])
    ticks = iter(range(100))
    rows = AU.audition(synth, tmp_path, clock=lambda: next(ticks) * 0.5, say=lambda m: None)
    assert [r["engine"] for r in rows] == ["kokoro", "server"]
    ok, bad = rows
    assert ok["first_s"] == 0.5 and ok["total_s"] == 1.0 and (tmp_path / ok["file"]).exists()
    assert "error" in bad and broken.calls == 1  # the failing engine is auditioned itself, not its backup
    with wave.open(str(tmp_path / ok["file"])) as w:
        assert w.getframerate() == 24000 and w.getnframes() == 3 + 3 * int(24000 * AU.GAP_S)
    page = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<h2>Kokoro</h2>" in page and "<h2>Speech server</h2>" in page and f'src="{ok["file"]}"' in page and "0.50 s" in page and "Failed" in page
    assert AU.SENTENCES[0] in page


def test_audition_only_filter_and_html_escaping(tmp_path):
    e = Eng("pocket", ["pocket:<b>me</b>"], prefix="pocket:")
    rows = AU.audition(M.MultiSynth([Eng("kokoro", ["af"]), e]), tmp_path, only={"pocket"}, say=lambda m: None)
    assert [r["engine"] for r in rows] == ["pocket"] and Path(tmp_path / rows[0]["file"]).exists()
    assert "<b>me</b>" not in (tmp_path / "index.html").read_text(encoding="utf-8")


# ---- the cloned voice sits beside the built-in voices; the list shows only the chosen voices ----
def test_cloned_voice_is_added_to_the_built_in_voices_not_instead_of_them():
    clone = Eng("chatterbox", ["clone"])
    builtin = Eng("kokoro", ["af_sky", "bm_george"])
    ready = threading.Event()
    synth = M.PendingClone(builtin, "http://x", "", 1.0, on_ready=ready.set, ensure=lambda u, l: None, make=lambda u, s: clone)
    assert ready.wait(5)
    assert [v.id for v in synth.voices] == ["clone", "af_sky", "bm_george"]
    synth.set_voice("af_sky")
    assert synth.engine == "kokoro" and synth("hi") and builtin.calls == 1 and clone.calls == 0
    synth.set_voice("clone")
    assert synth.engine == "chatterbox" and synth("hi") and clone.calls == 1


def test_choosing_the_clone_before_its_server_answers_uses_the_built_in_voice():
    go = threading.Event()
    builtin = Eng("kokoro", ["af_sky"])
    synth = M.PendingClone(builtin, "http://x", "", 1.0, ensure=lambda u, l: go.wait(5), make=lambda u, s: Eng("chatterbox", ["clone"]))
    assert synth.set_voice("clone").id == "clone" and synth.engine == "kokoro" and synth("hi") and builtin.calls == 1
    go.set()


def test_voice_list_shows_chosen_voices_your_own_and_the_current_one():
    vs = [M.Voice(i, i, "female", 0) for i in ("af_sky", "af_alloy", "clone", "pocket:bria", "pocket:me", "eleven:abc", "server:nova", "st1")]
    ids = lambda allowed, cur="": [v.id for v in M.shown_voices(vs, allowed, cur)]  # noqa: E731
    assert ids(["af_sky", "pocket:me"]) == ["af_sky", "clone", "pocket:me", "eleven:abc", "server:nova"]
    assert ids(["af_sky"], "st1") == ["af_sky", "clone", "eleven:abc", "server:nova", "st1"]
    assert ids([]) == ids(None) == [v.id for v in vs]


def test_default_voice_list_is_the_seven_picked_voices():
    from voice_assistant.config import Config

    assert Config().tts_voice_list == ["af_sky", "af_heart", "af_bella", "af_nicole", "bm_george", "am_michael", "bm_lewis"]
