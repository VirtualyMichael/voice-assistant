# Voice Assistant — milestone 1 prototype

## Install (Windows 10/11)

1. Download `VoiceAssistant-Setup-<version>.exe` from the repository's Releases page and run it. No admin rights needed.
   The installer is not code-signed, so Windows SmartScreen shows "Unknown publisher": choose *More info > Run anyway*.
2. On first start a setup guide checks your mic, speakers, GPU, a local AI server (LM Studio etc.) and VB-Cable, downloads
   the speech models only after you tick them (recognizer 122 MB, natural voices 305 MB, optional basic voice 64 MB and
   wake word 17 MB), and lets you choose local, Claude or OpenAI. It can be rerun from Settings or the Start menu.
3. Your conversation is remembered between sessions (stored on this PC only, `%APPDATA%\\VoiceAssistant\\conversation.json`;
   only your messages and the assistant's replies, never tool output). *New chat* erases it.

Build the installer yourself: `powershell -File packaging\\build.ps1 -Version 0.1.0` (needs PyInstaller and Inno Setup 6).
The build is gated on `VoiceAssistant.exe --selftest`, which checks that every dependency is inside the package.
Verified here: the packaged app starts with real models, a fresh profile shows the wizard, a second copy is refused, and the
installer installs, passes the self-test from the installed copy, then uninstalls leaving your data. Not verified: a clean
PC without Python or Visual C++ runtimes (the package bundles what it found on this machine), or a signed build.

Windows 11 voice assistant: hotkey (or optional wake word) → local streaming speech recognition →
streamed text in the window → reply spoken sentence by sentence. Local LM Studio endpoint by default;
any OpenAI-compatible cloud endpoint works with your own key. Audio and transcripts stay in memory only.

## Run

```
.venv\Scripts\python -m voice_assistant doctor          # what is installed / reachable
.venv\Scripts\python -m voice_assistant fetch-models    # asks before each download
.venv\Scripts\python -m voice_assistant                 # start the app
.venv\Scripts\python -m pytest                          # tests
```

Start LM Studio's local server (default `http://localhost:1234/v1`) and load a model; set `llm_model`
in `%APPDATA%\VoiceAssistant\config.json`. For a cloud endpoint set `llm_base_url`, `llm_model` and
`llm_api_key_env` (the *name* of an environment variable; the key itself is never written to disk).

Default hotkey `ctrl+alt+space`: starts listening when idle, stops everything otherwise.
Without models or a microphone the window still works as a typed chat.

## Interruption modes (dropdown / `interrupt_mode`)

| mode | speaking over the reply | hotkey while speaking |
|---|---|---|
| `voice` | interrupts and starts a new turn | stops |
| `hotkey` | ignored | stops |
| `ignore` | ignored | ignored (the Stop button still works) |

Use headphones with `voice`; speaker output can be picked up by the mic and count as barge-in.

## Audio devices and game voice

The window has pickers for microphone, an optional 2nd input (mixed into the first), speaker and an
optional 2nd output (same audio, e.g. headphones plus a VB-Cable input that a game or chat app uses as its mic).
Choices are saved by device name. A level meter shows what the mic hears, and "Test speaker" plays a phrase.
`listen_mode`: `push` (hotkey/Talk) or `always` (open mic). In `voice` interrupt mode, words that match what the
assistant is currently saying are treated as echo, but headphones or a cable output are still best.

## Window, wake word, hardware profile

- Dark chat window; **Pin on top** keeps it above games and other apps (remembered), plus an opacity slider in Settings.
- Wake word: tick it in Settings and set `wake_phrase` (default "hey assistant"), then restart. The phrase is tokenised
  with the KWS model's BPE model; `wake_threshold` trades sensitivity against false triggers. Applies in `push` listening mode.
- `python -m voice_assistant setup` is a guided first run (reports gaps, asks before downloading, picks devices).
  `doctor` and Settings show your GPU tier (under 8 / 8-12 / 12-20 / 20+ GB) and a model-size suggestion after the
  VRAM reserve you set for games.

## VR auto-switch

Settings → "Switch devices while SteamVR is running" reveals VR microphone/speaker pickers (hidden until enabled).
Every `vr_poll_s` seconds (default 3) the app checks for `vr_processes` (default `vrserver.exe`, `vrmonitor.exe`;
a scan takes ~10 ms) and switches after two consecutive matching checks. Turning the option off returns to the normal
devices. Process-name detection and device switching are unit-tested; not yet tried against a real SteamVR session.

## Game voice: forwarding your mic to the cable

Windows' "Listen to this device" is fixed to one mic, so it cannot follow a VR switch. Instead enable
**Send my mic to the 2nd output** (needs 2nd output = `CABLE Input`) and turn Windows' Listen off, otherwise others
hear you twice. The app then sends whichever mic is active (normal mic, or the VR mic while SteamVR runs) plus the
assistant's voice into the cable; the game keeps `CABLE Output` as its mic. "Mute game mic" in the header mutes only
what the game hears. Captured at 48 kHz for game quality; the forward buffer is capped at 250 ms. Verified with a test
tone through the real cable (arrives; silent when muted); not yet tried by ear in VRChat. SteamVR itself switches
Windows' *default* devices to the headset when it starts; this app uses named devices, so that does not affect it.

## Voices and speech quality

- `python -m voice_assistant fetch-models kokoro` adds the Kokoro model (~305 MB, 11 natural English voices); the app uses it
  automatically over the Piper fallback. Settings → Voice lists female and male voices (US and UK) and speaks a sample on
  change. Voice ids map to speaker ids; gender was checked by measuring pitch (female 154-207 Hz, male 98-141 Hz).
  Kokoro runs about 4x faster than real time on CPU (first sentence audible ~0.9 s after the reply starts).
- Text is normalised before speaking: numbers, prices, percentages, times, dates, ordinals, units, URLs, emoji and
  abbreviations; jargon like VRChat/SteamVR/GPU is spelled out. Add your own words with `tts_pronunciations` in config.json.
- Words cut off: playback used to close the device right after the last sample, which clipped the end of a sentence.
  Streams now stay open, add 120 ms lead-in after idle and 100 ms of tail, and abort cleanly on interrupt. Long sentences
  are split at 220 characters because TTS models truncate very long input.
- Confirmed by the user: Kokoro voices sound natural and words are no longer cut off.
- Known weak point: the small 20M recognizer often drops the first word of an utterance ("Hello" heard as "O").

## Agent tasks

The assistant can call tools in a loop (up to `max_tool_rounds`, default 8), shows each call as a small note in the chat,
and says "One moment." (`tool_filler`) when a task starts before any reply text.

| tool | what it does | gate |
|---|---|---|
| `get_time`, `system_status` | date/time, CPU and memory | none (read-only) |
| `set_timer`, `list_timers`, `cancel_timer` | timers and reminders, spoken when they finish | none |
| `list_directory`, `read_text_file` | files | approved folders only |
| `write_text_file` | create/overwrite a file | approved folders + confirm dialog |
| `open_url`, `launch_app` | open an http(s) link / a configured app (`app_shortcuts`) | confirm dialog |
| `read_clipboard`, `write_clipboard` | clipboard | confirm dialog |
| `web_search`, `fetch_page` | internet search and page text | off until "Let the agent search/read the web"; localhost/LAN blocked, redirects re-checked |
| `run_powershell` | a command inside an approved folder, 30 s limit | off until enabled; shows the exact command and confirms every time |

Text from web pages, files and the clipboard is marked as untrusted data in the model's prompt, and every state-changing
action needs your click, so a malicious page cannot make the agent act on its own. Verified live with the Gemma model:
time, timer (spoken when done), system status and web search worked; `open_url` correctly stopped at the confirm step.
Replies that use tools take about 5 s to start (tool round trip plus a longer prompt).

Device list refresh: PortAudio caches the device list at start-up and Windows renumbers devices when the headset powers
up, which sent the assistant's voice to the monitor after SteamVR started. A VR switch now closes all streams, re-reads
the device list, restarts the mic, and repeats once 6 s later (headset audio can appear late). Unit-tested with fakes and
simulated on/off in the window; confirmed fixed on SteamVR *close* by the user, start not yet re-tested.

VR switching now re-opens the speaker for the active profile, shows both mic and speaker in the chat note, and speaks
"VR mode on" / "Desktop mode" through the new speaker so you can hear which one is active (`vr_announce`).

## Listening cue and cut-offs

On hotkey, Talk button, wake word (and after a spoken reply when `follow_up` is on) the app plays a short two-note chime
and keeps the mic closed until it and a short gap (`listen_delay_s`) have finished, so the start of your sentence is
neither lost nor mixed with the chime. In `always` listen mode there is no chime. `end_of_speech_s` (default 1.0 s) is
the silence that ends your turn: raise it if you get cut off during a pause. `post_speech_guard_s` (0.3 s) is how long the
mic is ignored after the assistant finishes. Behaviour is covered by unit tests; the timings are untested by ear.

## Cloud models (OpenAI, Claude)

Settings → AI model: Local, OpenAI or Claude. "Set API key…" stores the key in Windows Credential Manager (an environment
variable named by `openai_key_env` / `anthropic_key_env` also works). Switching to a cloud provider asks once, because your
messages and any file/web/clipboard text the agent reads are sent to that company and may cost money. Claude uses the native
Messages API (streaming and tool use); OpenAI uses the chat-completions API, so any compatible endpoint (`openai_base_url`)
works. Defaults: Claude `claude-haiku-5-5`; set `openai_model` yourself. Both clients are covered by mocked-API tests; they
have not been run against the real services because no keys are saved.

## MCP tool servers

Add servers to `mcp_servers` in config.json (Settings → Open settings file), then restart:
`"mcp_servers": {"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\\\Docs"]}}`.
Their tools join the agent's tool list. Each server runs with a minimal environment (your secrets are not passed on),
tools not marked read-only ask before every call unless the server has `"auto_approve": true`, and their output is labelled
as untrusted data. Tested against a real stdio MCP server (discovery, confirm/read-only behaviour, secret isolation, a
failing server).

## Safe task

File tools (`list_directory`, `read_text_file`, `write_text_file`) work only inside folders added with
"Add approved folder…". Paths outside (including `..`, symlinks, alternate data streams) are rejected;
every write asks for confirmation (auto-declined after 60 s). No delete tool exists.

## Latency

Each turn shows first-text and first-audio time, tagged local/cloud. They are measured from the
recognizer's end-of-speech detection, which already includes its trailing-silence wait (0.8 s rule),
so add that when comparing with total perceived delay. `MetricsLog.summary()` gives medians.

## Status / not yet verified

- Unit and session tests pass with fakes. Models are downloaded and a loopback run (real Piper TTS audio
  fed to the real streaming recognizer, through `Session`, with a scripted LLM) worked: first text 47 ms,
  first audio 250 ms after end-of-speech. The synthetic voice was transcribed imperfectly ("Hello" -> "Ular"),
  so real accuracy needs measuring with a live microphone.
- Live microphone, speaker playback and LM Studio (gemma-4-12b-it-qat, reasoning off) confirmed working by the user.
- **Not yet verified:** the global hotkey, two simultaneous outputs/inputs with a real virtual cable, and `voice` interrupt mode with speakers.
- Wake word ("hey assistant") confirmed on a live mic by the user, plus synthetic-speech checks (ignores other
  sentences). False-trigger rate in games and noise is untested.
- Virtual cable verified with VB-Cable: assistant speech sent to `CABLE Input` was captured from `CABLE Output` and
  recognised; two outputs at once and two mixed inputs also open and run. Game voice setup: Speaker = your headphones,
  2nd output = `CABLE Input`, and set the game/chat app's microphone to `CABLE Output`. Not yet tried inside a real game.
- Model URLs point to k2-fsa/sherpa-onnx releases. Audit model and dependency licenses before packaging.
- Bionic integration: not started (no confirmed supported task-control interface).
- No NVIDIA GPU was detected on this machine (`nvidia-smi` absent); VRAM tiers and the setup wizard are a later milestone.
- Python 3.11 venv (`.venv`) is used because 3.14 wheels for the audio stack were not confirmed.
