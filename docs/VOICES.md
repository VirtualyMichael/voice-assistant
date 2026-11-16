# More lifelike voices

The built-in voices (Kokoro, Supertonic, Piper) are small and run on the CPU, but they do not sound like the voices in ChatGPT or Claude.
Three more engines can now join the same Voice list. Each one is off until you set it up, and each falls back to the built-in voice
for any sentence it cannot speak.

| Engine | Where it runs | What it sends out | How to switch it on |
|---|---|---|---|
| **Pocket TTS** (Kyutai, through sherpa-onnx) | On this PC, CPU | Nothing | Settings > Voice > More lifelike voices > **Download Pocket** (about 160 MB), or `python -m voice_assistant fetch-models pocket`. Then **Add a voice from a recording...** |
| **Speech server** (OpenAI's `/v1/audio/speech`) | OpenAI, or a server you run | With an online address: the text of every spoken reply | Fill in **Address**, **Model**, **Voices** and, for a paid service, **Set speech server key...** |
| **ElevenLabs** | ElevenLabs (online) | The text of every spoken reply; uses your account's credits | **Set ElevenLabs key...** |

All changes apply after a restart. New voices appear in the one Voice list, after the built-in ones, with the engine in brackets:
`Bria (Pocket)`, `coral (Speech server)`, `Rachel (ElevenLabs)`.

## Pocket: a voice from a short recording

Pocket speaks in the voice of a recording: 5 to 15 seconds of one person speaking clearly, without music, as a `.wav` file.

- Voices are the sample recordings that come with the model (any `.wav` inside `<models>/pocket`) plus every `.wav` in
  `%APPDATA%\VoiceAssistant\voices`. **Add a voice from a recording...** copies a file there (it is checked first, and never overwrites one with the same name).
  You can also drop files into that folder yourself. Voice ids are `pocket:<file name>`.
- Only use a voice you have the right to use: your own, or one whose owner agreed. Do not copy real people's voices without their consent.
- The model loads the first time a Pocket voice speaks (or in the background a few seconds after start-up when a Pocket voice is the chosen one).
- The engine is skipped silently when `<models>/pocket` is missing.
- The full-precision model is used. An int8 version (`sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2`, 93 MB) exists on the same release page; extracting it into the
  `pocket` folder instead also works (int8 files are preferred when both are present).

## Speech server

Settings (in `config.json`, or the Voice tab):

| Setting | Default | Meaning |
|---|---|---|
| `tts_server_url` | empty (off) | `https://api.openai.com/v1`, or a server on this PC such as Kokoro-FastAPI (`http://127.0.0.1:8880/v1`), Orpheus-FastAPI, Chatterbox-TTS-API or Fish Speech |
| `tts_server_model` | `gpt-4o-mini-tts` | The model name the server expects |
| `tts_server_voices` | `alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse` | Comma list; each becomes a voice `server:<name>` |
| `tts_server_instructions` | empty | Speaking style, e.g. "Warm and relaxed, like a friend". Sent only when not empty (gpt-4o-mini-tts reads it; other servers may ignore it) |

- The key is kept in Windows Credential Manager under the name `tts_server`, never in `config.json` or the log. For `api.openai.com` without its own key, the OpenAI key
  the chat already uses is taken (saved key, then the `OPENAI_API_KEY` environment variable). With `api.openai.com` and no key at all, the engine is absent.
- The request asks for `response_format: "pcm"` (24 kHz, 16-bit, mono). A reply that starts with a WAV header is read as a WAV file instead, so servers that
  only send WAV also work. The speaking speed setting is sent as `speed` when it is not 1.0.
- Time limits: 5 s to connect, 30 s for the reply.

## ElevenLabs

- Key in Windows Credential Manager under `elevenlabs` (or the `ELEVENLABS_API_KEY` environment variable). No key: the engine is absent.
- `elevenlabs_model`, default `eleven_flash_v2_5` (their fast model).
- Your account's voices are listed once at start-up in the background (`GET /v1/voices`). If that fails, ElevenLabs has no voices and a warning is logged; the rest of the app is unaffected.
  A voice chosen earlier stays chosen while the list is loading. Voice ids are `eleven:<voice_id>`.
- Speech comes from `POST /v1/text-to-speech/{voice_id}/stream?output_format=pcm_24000`. The speed setting is not sent to ElevenLabs.

## Never silent

When Pocket, the speech server, ElevenLabs or the Chatterbox cloned voice fails on a sentence (an error, a time-out, or for example Chatterbox Turbo, which is
English-only, given Polish text), that sentence is spoken by the built-in voice and one warning is logged (without the sentence). After 3 failures in a row the built-in
voice speaks everything for 60 seconds before the engine is tried again, so a server that is down does not add a time-out to every sentence.

## Comparing voices by ear

`python -m voice_assistant audition [--out DIR] [--only pocket,server,elevenlabs]` writes one WAV per available voice (default folder `voice-samples/audition`) and an `index.html`
with a player per voice, grouped by engine. Every voice says the same three sentences: a short acknowledgement, a two-sentence weather answer, and a question. Each row shows the
**first sound** time (how long the first sentence took to make, model loading not counted) and the time for all three. Online engines are really called, so ElevenLabs uses credits.
A failing voice shows its error instead of being replaced by the built-in voice. If a Chatterbox server is configured, audition starts it and waits up to 3 minutes.

## The phone

The phone plays the audio this PC makes, so every new voice works on the phone too. The new engines return the same `(samples, sample rate)` as the built-in ones and go
through the same speaker and phone link, so nothing about the phone connection changed.

## Not built: starting a sentence before it has fully arrived

Playing an online voice's sentence while it is still downloading would make the first sound come sooner. It was left out because both players need the whole sentence:
the phone link announces each sentence's length before sending it, and the PC speaker adds a short silence after each piece, which would put gaps inside sentences.
Doing it properly means changing the phone connection; for now each sentence is fetched whole (sentences are short, so the wait is roughly one sentence).

## What was verified and what was not

- Verified with fake servers and fake models only (automated tests): voice lists, picking a voice of each engine, the request each engine sends, PCM and WAV decoding,
  where keys are looked up, the fall-back and the 60-second pause, the audition page, the add-a-recording button.
- Verified on this PC: the audition command with the installed Piper voice, and the fall-back from a speech server that is not running to the built-in voice.
- Verified on this PC (Ryzen 7800X3D, CPU only): Pocket TTS downloads with `fetch-models pocket`, its files match what the engine looks for, and it speaks with its
  sample voices and with a recording added through the voices folder. First sentence ready in about 0.4 to 0.7 s (Kokoro about 0.3 s, the Chatterbox clone on the
  Radeon about 1.2 s). Every Pocket sample was transcribed back almost word for word by the accurate recognizer, so the speech is clear; one sample dropped its first word.
- **Not verified**: how natural Pocket sounds to a person (nobody has listened yet); any real OpenAI, ElevenLabs or local speech server.
- Pocket licence: the archive's `LICENSE` is CC BY 4.0 but its README says non-commercial. See `THIRD_PARTY_NOTICES.md`; keep it a user-chosen download.

## Background: other models people recommend (not verified by us)

| Model | Why it is on the list | How to try it here |
|---|---|---|
| **Chatterbox** (Resemble AI) | Natural, clones a voice from a short sample. | Built in (`chatterbox_url`), or through Chatterbox-TTS-API as a speech server. |
| **Kokoro-FastAPI** | The Kokoro voices as an OpenAI-style server, often on a graphics card. | Speech server. |
| **Orpheus** | Scores very high for naturalness in one comparison; slower to first audio. | Speech server through Orpheus-FastAPI. |
| **Fish Speech / Fish Audio S2** | Ranked highly among open-weight models in 2026 write-ups. | Speech server, if its OpenAI-compatible API is used. |
| **CosyVoice 2** | Named for real-time streaming. | Would need an OpenAI-style server wrapper. |

These come from comparison sites of mixed quality ([MarkTechPost, May 2026](https://www.marktechpost.com/2026/05/30/best-text-to-speech-tts-models-in-2026-a-benchmark-based-comparison/),
[BentoML](https://www.bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models)); read each model's licence on its own page before relying on it.
The graphics card here is an AMD Radeon, so servers that need CUDA will not use it; check each server's AMD/ROCm or CPU support.
