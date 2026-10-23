<div align="center">

# Voice Assistant

**A private, hands-free voice assistant for Windows. Local speech in, streamed replies out, and an agent that acts on your PC only when you allow it.**

![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D4?logo=windows&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Local first](https://img.shields.io/badge/speech-local%20first-2ea44f)
![Models](https://img.shields.io/badge/models-LM%20Studio%20%7C%20Ollama%20%7C%20OpenAI%20%7C%20Claude-6c8cff)

[Install](#install) · [Features](#features) · [Using the assistant](#using-the-assistant) · [Agent and tools](#agent-and-tools) · [VR and games](#vr-and-games) · [Odysseus](#odysseus-optional-off-by-default) · [Related projects](#related-projects)

</div>

## Contents

- [Overview](#overview)
- [Install](#install)
  - [Windows installer](#windows-installer)
  - [Run from source](#run-from-source)
- [Features](#features)
- [Using the assistant](#using-the-assistant)
  - [Settings tabs](#settings-tabs)
  - [Interruption modes](#interruption-modes)
  - [Listening cue and cut-offs](#listening-cue-and-cut-offs)
  - [Window, wake word, hardware profile](#window-wake-word-hardware-profile)
  - [Ask first](#ask-first)
- [Voices and audio](#voices-and-audio)
  - [Voices and speech quality](#voices-and-speech-quality)
  - [Using your own voice model](#using-your-own-voice-model)
  - [Audio devices and game voice](#audio-devices-and-game-voice)
- [VR and games](#vr-and-games)
  - [VR auto-switch](#vr-auto-switch)
  - [Forwarding your mic to the cable](#forwarding-your-mic-to-the-cable)
  - [Hearing yourself twice on the cable](#hearing-yourself-twice-on-the-cable)
  - [VRChat control (OSC)](#vrchat-control-osc)
- [Agent and tools](#agent-and-tools)
  - [Agent tasks](#agent-tasks)
  - [Safe file access](#safe-file-access)
  - [Opening apps](#opening-apps)
  - [Routines, music actions, interpreter and more](#routines-music-actions-interpreter-and-more)
  - [Skills (Bionic / Claude format)](#skills-bionic--claude-format)
  - [MCP tool servers](#mcp-tool-servers)
  - [Odysseus (optional, off by default)](#odysseus-optional-off-by-default)
- [AI models](#ai-models)
  - [Cloud models (OpenAI, Claude)](#cloud-models-openai-claude)
- [Performance and status](#performance-and-status)
  - [Latency](#latency)
  - [Status and not yet verified](#status-and-not-yet-verified)
- [Related projects](#related-projects)
- [Notices](#notices)

## Overview

Windows 11 voice assistant: hotkey (or optional wake word) → local streaming speech recognition → streamed text in the window → reply spoken sentence by sentence. Local LM Studio endpoint by default; any OpenAI-compatible cloud endpoint works with your own key. Audio and transcripts stay in memory only.

**At a glance**

- A hotkey, button or optional wake word starts a turn; speech is recognised on your PC and the reply is spoken sentence by sentence.
- Works with a local model (LM Studio, llama.cpp, Ollama, any OpenAI-compatible server) or your own OpenAI / Claude key.
- Agent tools for apps, files, timers, music, smart home, screen help and more. Anything that changes something asks first.
- Built for games and VR: virtual-cable voice routing, SteamVR auto-switch, VRChat OSC control.
- Optional: skills, MCP tool servers, hand-off to Claude Code / Codex, and a link to [Odysseus](docs/ODYSSEUS.md).

## Install

### Windows installer

1. Download `VoiceAssistant-Setup-<version>.exe` from the repository's Releases page and run it. No admin rights needed.
   The installer is not code-signed, so Windows SmartScreen shows "Unknown publisher": choose *More info > Run anyway*.
2. On first start a setup guide checks your mic, speakers, GPU, a local AI server (LM Studio etc.) and VB-Cable, downloads
   the speech models only after you tick them (recognizer 122 MB, natural voices 305 MB, optional basic voice 64 MB and
   wake word 17 MB), and lets you choose local, Claude or OpenAI. It can be rerun from Settings or the Start menu.
   The **accurate recognizer** (Parakeet TDT 0.6B v2, 460 MB, tick it in the wizard or run `fetch-models asr2`) re-checks every
   sentence after you finish speaking. On 10 spoken test sentences in 4 synthetic voices plus 2 real recordings it cut the word
   error rate from 33% to 3% (the fast model drops or garbles the first words) and adds about 0.2 s per sentence on a Ryzen 7800X3D.
   The fast streaming model still drives the live text preview and end-of-sentence detection.
3. Your conversation is remembered between sessions (stored on this PC only, `%APPDATA%\\VoiceAssistant\\conversation.json`;
   only your messages and the assistant's replies, never tool output). *New chat* erases it.

Build the installer yourself: `powershell -File packaging\\build.ps1 -Version 0.1.0` (needs PyInstaller and Inno Setup 6).
The build is gated on `VoiceAssistant.exe --selftest`, which checks that every dependency is inside the package.
Verified: the packaged app starts with real models, a fresh profile shows the wizard, a second copy is refused, and the
installer installs, passes the self-test from the installed copy, then uninstalls leaving your data. Not verified: a clean
PC without Python or Visual C++ runtimes (the package bundles what it found on this machine), or a signed build.

### Run from source

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

## Features

Everything below is built in. Items marked *(off by default)* need switching on in Settings, and anything that changes something on your PC asks first.

**Talking and listening**
- Hands-free by hotkey, a **Talk** button or a **wake word** (default "hey assistant"; a one-word phrase such as "Jarvis" also answers to "Hey Jarvis" and "OK Jarvis"). A *Test wake word* button and a live *Wake sensitivity* slider help you tune it.
- Streaming speech recognition with an optional **accurate recognizer** (Parakeet) that re-checks each sentence, so words like "into" are not heard as "in two".
- Replies stream as text and are spoken sentence by sentence, with a listening chime, a configurable follow-up listening time, and three interrupt modes (voice, hotkey only, ignore).
- Optional **spoken approval** *(off by default)*: confirmations are read aloud and you answer "yes" or "no".
- Conversation memory between sessions (stored only on your PC; *New chat* erases it).

**Voices**
- Natural local voices: **Kokoro** (v0.19 and v1.0, 28 English voices), **Supertonic v3**, **Piper Kusal** and a basic Piper voice, all in one voice list, male and female.
- A one-click **Jarvis-style preset** (British voice, calm butler manner, "Jarvis" wake phrase), and a *use my own voice model* option for a sherpa-onnx VITS/Piper voice you have the right to use.

**AI models**
- **Local**: LM Studio, llama.cpp, **Ollama** or any **custom OpenAI-compatible server** (your own address, model and key).
- **Cloud**: OpenAI and Claude with your own key (stored in Windows Credential Manager, never in the settings file).
- Skills in the Bionic / Claude `SKILL.md` format, with PDF reference search; MCP tool servers (including importing them from LM Studio).
- **Memory profile**: tell it who you are and how you like things; it can also propose facts to remember (you confirm each one).
- Optional **Jarvis-style butler personality**.

**Things it can do for you** (it asks before actions that change things)
- Open apps by name (it switches to a window that is already open instead of starting a second copy), set timers, read or write the clipboard, search and read the web *(off by default)*, work with files and PDFs in folders you approve, run PowerShell *(off by default)*.
- **Music and volume**: play, pause, next and previous through Windows media sessions; exact volume; one app's volume ("set YouTube Music to 40"); "play Maps by Maroon 5" in YouTube Music, done by searching and pressing play on the YouTube Music web page itself in a YouTube Music window the assistant keeps (sign in there once): no screenshots, no AI vision, no cost, and it checks the right song actually started. Optionally install the small, readable browser extension in `extension/` (see its README) and the same thing is done in your own signed-in YouTube Music tab or app window instead. With the extension it can also save your whole library (playlist names and every song in them) on your PC, so a spoken "Continuum by SLVM Lord" is matched to the exact saved spelling, a different artist's song with the same title is never played by mistake, and when the page is unclear your own language model reads the whole result list to decide.
- **Smart home**: **Govee** lights (colour, brightness, on/off) over your network or Govee's cloud with your own key, and **Home Assistant** (which brings in Google Home / Nest, Alexa-connected devices, Matter, Hue and more; locks and alarms always ask).
- **Screen help**: "what's on my screen?", or "look at VRChat and translate the text", using the vision model you already run; answers come in your own language.
- **Computer use** *(experimental, off by default)*: a step-by-step loop that looks at an app window, clicks buttons by reading their text (Windows' built-in OCR), types, presses keys and checks the result. It asks before touching each window and refuses terminals, password managers and security prompts.
- **Hand coding tasks to Claude Code or Codex** *(off by default)*: you approve the exact text first, with daily request and dollar limits and a usage readout.
- Notes, weather, routines (named multi-step actions such as "good night").
- **Odysseus** *(optional, off by default)*: hand big agentic jobs to your own [Odysseus](https://github.com/odysseus-dev/odysseus) workspace, or use the assistant purely as the voice for it. See [docs/ODYSSEUS.md](docs/ODYSSEUS.md).

**Games and VR**
- Pin the window on top of other apps; use two microphones and two speakers; send your voice and the assistant's voice into a virtual cable (VB-Cable) so friends in VRChat hear both; mute the game mic from the window.
- **SteamVR auto-switch**: moves the microphone and headset to your VR devices when SteamVR starts and back when it closes, and warns about Windows "Listen to this device" double voices.

**Look and feel**
- A modern dark window with optional **frosted glass** (Windows 11 acrylic or Mica), tint and accent colours, whole-window opacity, and settings on a clean sidebar page.

**Installing and trust**
- A guided first-run setup, an installer with a self-test gate, models downloaded only after you tick them, no analytics or tracking, and your audio never saved. Actions that change things ask first.
- Windows 10/11 (frosted glass needs Windows 11). The installer is not code-signed yet.

## Using the assistant

### Settings tabs

Settings opens as a full page with a sidebar: **Voice** (voice picker, wake word, listening, approvals), **Audio** (microphone and speaker devices), **VR** (game voice, mic forwarding, SteamVR switching), **AI** (model provider, key, memory, web/shell abilities), **Skills**, **Apps** (app list, folders, computer control), **Home** (Govee, Home Assistant), **Memory** (your profile), **Delegate** (Claude Code / Codex with limits), **Look** (glass, colours, opacity) and **General** (setup guide, settings file).

### Interruption modes

| mode | speaking over the reply | hotkey while speaking |
|---|---|---|
| `voice` | interrupts and starts a new turn | stops |
| `hotkey` | ignored | stops |
| `ignore` | ignored | ignored (the Stop button still works) |

Use headphones with `voice`; speaker output can be picked up by the mic and count as barge-in.

### Listening cue and cut-offs

Settings has three timing controls: *Keep listening after a reply* (0 = off), *Wait for me to start speaking* and *End of sentence after silence*.

On hotkey, Talk button, wake word (and after a spoken reply when `follow_up` is on) the app plays a short two-note chime
and keeps the mic closed until it and a short gap (`listen_delay_s`) have finished, so the start of your sentence is
neither lost nor mixed with the chime. In `always` listen mode there is no chime. `end_of_speech_s` (default 1.0 s) is
the silence that ends your turn: raise it if you get cut off during a pause. `post_speech_guard_s` (0.3 s) is how long the
mic is ignored after the assistant finishes. Behaviour is covered by unit tests; the timings are untested by ear.

### Window, wake word, hardware profile

- Dark chat window; **Pin on top** keeps it above games and other apps (remembered), plus an opacity slider in Settings.
- Wake word: tick it in Settings and set `wake_phrase` (default "hey assistant"), then restart. The phrase is tokenised
  with the KWS model's BPE model; `wake_threshold` trades sensitivity against false triggers. Applies in `push` listening mode.
- `python -m voice_assistant setup` is a guided first run (reports gaps, asks before downloading, picks devices).
  `doctor` and Settings show your GPU tier (under 8 / 8-12 / 12-20 / 20+ GB) and a model-size suggestion after the
  VRAM reserve you set for games.

### Ask first

By default the assistant asks before it opens a link, takes a screenshot, reads or replaces your clipboard, or runs a saved routine (opening an app has its own box in the Apps tab). Untick any of these to let it just do it, for example if you are tired of screenshot prompts for the translate feature. The tooltips explain the risk of each. Shell commands, writing files, handing jobs to Claude/Codex, controlling app windows and MCP tools always ask, and unticking a box never widens what is allowed (only http and https links can ever be opened).

## Voices and audio

### Voices and speech quality

- `python -m voice_assistant fetch-models kokoro` adds the Kokoro model (~305 MB, 11 natural English voices); the app uses it
  automatically over the Piper fallback. Settings → Voice lists female and male voices (US and UK) and speaks a sample on
  change. Voice ids map to speaker ids; gender was checked by measuring pitch (female 154-207 Hz, male 98-141 Hz).
  Kokoro runs about 4x faster than real time on CPU (first sentence audible ~0.9 s after the reply starts).
- Text is normalised before speaking: numbers, prices, percentages, times, dates, ordinals, units, URLs, emoji and
  abbreviations; jargon like VRChat/SteamVR/GPU is spelled out. Add your own words with `tts_pronunciations` in config.json.
- Words cut off: playback used to close the device right after the last sample, which clipped the end of a sentence.
  Streams now stay open, add 120 ms lead-in after idle and 100 ms of tail, and abort cleanly on interrupt. Long sentences
  are split at 220 characters because TTS models truncate very long input.
- Verified: Kokoro voices sound natural and words are no longer cut off.
- Known weak point: the small 20M recognizer often drops the first word of an utterance ("Hello" heard as "O").

### Using your own voice model

Settings > Voice > **Use my own voice model...** loads a voice from a folder you choose (restart to apply; **Back to built-in voices** undoes it). The folder must hold a sherpa-onnx **VITS/Piper** voice: the model `.onnx`, `tokens.txt` and an `espeak-ng-data` folder (plus `lexicon.txt` if the voice has one). Voices in other formats (RVC, XTTS and similar cloning tools) are not supported.

Only use voices you have the right to use: ones you recorded or trained yourself, or ones whose licence allows it. This project does not provide, host or link to voices that imitate real people.

### Audio devices and game voice

The window has pickers for microphone, an optional 2nd input (mixed into the first), speaker and an
optional 2nd output (same audio, e.g. headphones plus a VB-Cable input that a game or chat app uses as its mic).
Choices are saved by device name. A level meter shows what the mic hears, and "Test speaker" plays a phrase.
`listen_mode`: `push` (hotkey/Talk) or `always` (open mic). In `voice` interrupt mode, words that match what the
assistant is currently saying are treated as echo, but headphones or a cable output are still best.

## VR and games

### VR auto-switch

Settings → "Switch devices while SteamVR is running" reveals VR microphone/speaker pickers (hidden until enabled).
Every `vr_poll_s` seconds (default 3) the app checks for `vr_processes` (default `vrserver.exe`, `vrmonitor.exe`;
a scan takes ~10 ms) and switches after two consecutive matching checks. Turning the option off returns to the normal
devices. Process-name detection and device switching are unit-tested; not yet tried against a real SteamVR session.

### Forwarding your mic to the cable

Windows' "Listen to this device" is fixed to one mic, so it cannot follow a VR switch. Instead enable
**Send my mic to the 2nd output** (needs 2nd output = `CABLE Input`) and turn Windows' Listen off, otherwise others
hear you twice. The app then sends whichever mic is active (normal mic, or the VR mic while SteamVR runs) plus the
assistant's voice into the cable; the game keeps `CABLE Output` as its mic. "Mute game mic" in the header mutes only
what the game hears. Captured at 48 kHz for game quality; the forward buffer is capped at 250 ms. Verified with a test
tone through the real cable (arrives; silent when muted); not yet tried by ear in VRChat. SteamVR itself switches
Windows' *default* devices to the headset when it starts; this app uses named devices, so that does not affect it.

### Hearing yourself twice on the cable

Cause: Windows' own *Listen to this device* was still switched on for the desktop mic and pointed at
`CABLE Input`, so the audio service (svchost.exe) fed the desktop mic into the cable while this app forwarded the *active*
mic (the headset mic in VR) into the same cable. Both reached the game. Windows' setting cannot follow the VR switch, so
it must be off whenever *Send my mic to the 2nd output* is on.

The app now checks this (Settings > VR > *Check for double voice*, also at start-up and after each VR switch). If Windows
is feeding a mic into the cable you forward into, it says so and names the mic; *Open Windows sound settings* opens the
Recording tab (mic > Properties > Listen > untick). The tab also shows what is routed right now, and only the active
profile's mic and speaker are ever open: VR = headset mic and headset speakers, desktop = desktop mic and speakers.

Verified: after unticking Listen, only one copy of your voice is heard.

Also hardened: restarts of the microphone pipeline are serialised (before, overlapping restarts could leave a second
copy running; with 12 simultaneous restarts the old code leaked 24 streams in a test and the new code keeps exactly 2),
VR switches that arrive in quick succession are collapsed so only the newest is applied, the same mic is never opened
twice, and forwarding the cable's own output back into the cable is refused.

### VRChat control (OSC)

Say "tell VRChat I'll be right back", "turn my hat on" or "mute me in VRChat": the assistant types in the chatbox, flips avatar toggles and presses a few controls through VRChat's own OSC feature. Switch it on in Settings > VR and in VRChat (Action Menu > Options > OSC > Enabled). Full guide, including how avatar parameters work and troubleshooting: [docs/VRCHAT.md](docs/VRCHAT.md).

## Agent and tools

### Agent tasks

The assistant can call tools in a loop (up to `max_tool_rounds`, default 8), shows each call as a small note in the chat,
and says "One moment." (`tool_filler`) when a task starts before any reply text.

| tool | what it does | gate |
|---|---|---|
| `get_time`, `system_status` | date/time, CPU and memory | none (read-only) |
| `set_timer`, `list_timers`, `cancel_timer` | timers and reminders, spoken when they finish | none |
| `list_directory`, `read_text_file` | files | approved folders only |
| `write_text_file` | create/overwrite a file | approved folders + confirm dialog |
| `open_url` | open an http(s) link | confirm dialog |
| `launch_app`, `list_apps` | open any installed app by name (the Start-menu list is read automatically, about 200 apps, so no setup), or one you added in Settings > Apps | confirm dialog (switch off with *Ask before opening an app*) |
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
simulated on/off in the window; confirmed fixed on SteamVR *close*, start not yet re-tested.

VR switching now re-opens the speaker for the active profile, shows both mic and speaker in the chat note, and speaks
"VR mode on" / "Desktop mode" through the new speaker so you can hear which one is active (`vr_announce`).

### Safe file access

File tools (`list_directory`, `read_text_file`, `write_text_file`) work only inside folders added with
"Add approved folder…". Paths outside (including `..`, symlinks, alternate data streams) are rejected;
every write asks for confirmation (auto-declined after 60 s). No delete tool exists.

### Opening apps

Apps open after a confirmation by default. Turn it off in Settings > Apps (*Ask before opening an app*), or tick *Don't ask again when opening apps* in the confirmation box itself. Links, clipboard access, file writes and shell commands keep asking regardless.

The assistant reads the Start-menu app list at start-up (`Get-StartApps`: desktop apps, Store apps and browser-installed web
apps such as YouTube Music), so "open YouTube Music" works without configuration. Matching ignores the punctuation speech recognition adds ("Open YouTube Music."), filler words ("the Chrome browser please"), extra or missing words ("creator companion"), split or joined words and small mis-hearings ("calculater"); uninstallers and help entries never win. It tolerates spoken names ("steam vr"
finds SteamVR, exact spelling beats look-alikes such as Notepad vs Notepad++, and close calls make it ask which one you mean).
It can only start apps from that list or from your own list, never text the AI makes up. If something is missing, Settings >
Apps lets you add any `.exe`, `.lnk` or `.bat` under a spoken name; those take priority over the automatic list. *Rescan*
refreshes the list after installing something.

### Routines, music actions, interpreter and more

- **Routines:** "make a good night routine that turns the lights off and pauses the music" saves several actions under one phrase (you confirm it first); "good night" then runs them. Routines cannot contain shell commands, delegated jobs or computer control.
- **Music actions** (need the browser extension): "like this song", "play more like this" (YouTube Music's radio for it), "shuffle", "what's playing", and "stop the music in 30 minutes". Each reports what the page showed afterwards.
- **Quiet music while I talk** (Settings > Audio): lowers only the music apps' own volume while the assistant listens and speaks, then restores it.
- **Interpreter mode:** "translate everything I say into Japanese" until you say stop; with VRChat control on it can type each translation in the chatbox.
- **"Why didn't that work?"** The assistant looks up the last failed action and explains it in plain words.

### Skills (Bionic / Claude format)

A skill is a folder with a `SKILL.md` (front matter `name` and `description`, then instructions) plus optional reference files,
the same layout Bionic and Claude use, so skills made there work here unchanged. **Settings > Skills > Add folder** accepts a
skill folder, a folder of skills, or a project with a `skills/` subfolder; untick a skill to switch it off. Only each skill's
name and description go into the prompt; when a question fits, the assistant calls `use_skill` to load the full instructions,
then `read_skill_file`, `search_skill_pdf` and `read_skill_pdf` to use its reference files (a skill's folder is read-only and
cannot be escaped). PDFs are indexed in the background and cached in `%APPDATA%\VoiceAssistant\cache\pdf` (a 181-page
manual takes about 6 s the first time, instantly after). `disable-model-invocation: true` skills are not advertised but can be
named explicitly. PDF reading also works for any PDF in an approved folder (`search_pdf`, `read_pdf`).

Skills that depend on an MCP server (for example a science tutor with its own local server) need that server too:
**Settings > Skills > Import tool servers (MCP) from LM Studio** reads LM Studio/Bionic's `mcp.json`, lets you tick the servers to
copy (and optionally let one run without asking each time), and connects them at the next start. Tested with the
real `garage-mechanic` skill and its two owner's manuals (search found the specifications page) and with the real
`science-teacher` server (4 tools, lists the Chemistry 2e textbook). Not tested: the local AI model choosing these tools by
itself, because no model server was running at the time.

### MCP tool servers

Add servers to `mcp_servers` in config.json (Settings → Open settings file), then restart:
`"mcp_servers": {"files": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\\\Docs"]}}`.
Their tools join the agent's tool list. Each server runs with a minimal environment (your secrets are not passed on),
tools not marked read-only ask before every call unless the server has `"auto_approve": true`, and their output is labelled
as untrusted data. Tested against a real stdio MCP server (discovery, confirm/read-only behaviour, secret isolation, a
failing server).

**Ready-made servers: [My MCP](https://github.com/VirtualyMichael/my-mcp).** A separate repo of small servers that install one at a time
(`python install.py <name>` registers the server in this app's `mcp_servers` and copies its skill). Currently:
`alcohol-tracker`, which lets you say "I just had a beer" (it reads the drink back, you say yes, it logs it to your
Alcohol Ingestion Tracker, and "undo" removes only what it added in the last 10 minutes) and ask for detailed reports by
time and amount. It cannot edit or delete anything else. Its skill is `alcohol-tracker` (picked up from `~/.lmstudio/skills`).

### Odysseus (optional, off by default)

Hand big agentic jobs (research, documents, email, calendar, shell work) to your own [Odysseus](https://github.com/odysseus-dev/odysseus)
workspace, or use this app purely as the voice and reply system while Odysseus does every action. Three modes: **alone**,
**with Odysseus**, **voice only**. Nothing is bundled: you install and run Odysseus yourself and enter its address, an API
token (kept in Windows Credential Manager) and your model endpoint in Settings > AI. Every hand-off asks first by default.
Setup and safety notes: [docs/ODYSSEUS.md](docs/ODYSSEUS.md).

## AI models

### Cloud models (OpenAI, Claude)

Settings → AI model: Local, OpenAI or Claude. "Set API key…" stores the key in Windows Credential Manager (an environment
variable named by `openai_key_env` / `anthropic_key_env` also works). Switching to a cloud provider asks once, because your
messages and any file/web/clipboard text the agent reads are sent to that company and may cost money. Claude uses the native
Messages API (streaming and tool use); OpenAI uses the chat-completions API, so any compatible endpoint (`openai_base_url`)
works. Defaults: Claude `claude-haiku-5-5`; set `openai_model` yourself. Both clients are covered by mocked-API tests; they
have not been run against the real services because no keys are saved.

## Performance and status

### Latency

Each turn shows first-text and first-audio time, tagged local/cloud. They are measured from the
recognizer's end-of-speech detection, which already includes its trailing-silence wait (0.8 s rule),
so add that when comparing with total perceived delay. `MetricsLog.summary()` gives medians.

### Status and not yet verified

- Unit and session tests pass with fakes. Models are downloaded and a loopback run (real Piper TTS audio
  fed to the real streaming recognizer, through `Session`, with a scripted LLM) worked: first text 47 ms,
  first audio 250 ms after end-of-speech. The synthetic voice was transcribed imperfectly ("Hello" -> "Ular"),
  so real accuracy needs measuring with a live microphone.
- Live microphone, speaker playback and LM Studio (gemma-4-12b-it-qat, reasoning off) confirmed working.
- **Not yet verified:** the global hotkey, two simultaneous outputs/inputs with a real virtual cable, and `voice` interrupt mode with speakers.
- Wake word ("hey assistant") confirmed on a live mic, plus synthetic-speech checks (ignores other
  sentences). False-trigger rate in games and noise is untested.
- Virtual cable verified with VB-Cable: assistant speech sent to `CABLE Input` was captured from `CABLE Output` and
  recognised; two outputs at once and two mixed inputs also open and run. Game voice setup: Speaker = your headphones,
  2nd output = `CABLE Input`, and set the game/chat app's microphone to `CABLE Output`. Not yet tried inside a real game.
- Model URLs point to k2-fsa/sherpa-onnx releases. Audit model and dependency licenses before packaging.
- Bionic integration: not started (no confirmed supported task-control interface).
- No NVIDIA GPU was detected on this machine (`nvidia-smi` absent); VRAM tiers and the setup wizard are a later milestone.
- Python 3.11 venv (`.venv`) is used because 3.14 wheels for the audio stack were not confirmed.

## Related projects

| Project | What it is |
|---|---|
| [My MCP](https://github.com/VirtualyMichael/my-mcp) | Individually installable MCP servers and skills for this app and other local AI apps. |
| [ytmusic-control](https://github.com/VirtualyMichael/ytmusic-control) | Control the YouTube Music you are signed in to from a CLI, an agent skill or an MCP server, through a small browser extension. |
| [alcohol-tracker](https://github.com/VirtualyMichael/alcohol-tracker) | Local Windows app for logging drinks and viewing estimated effect timelines (an optional skill in My MCP talks to it). |
| [vr-audio-auto-switcher](https://github.com/VirtualyMichael/vr-audio-auto-switcher) | Tray app that switches audio devices (and Discord) between desktop and VR when SteamVR starts. |
| [playspace-combiner](https://github.com/VirtualyMichael/playspace-combiner) | SteamVR playspace combiner: pose-correction driver and dashboard overlay. |
| [Odysseus](https://github.com/odysseus-dev/odysseus) | Third-party self-hosted agent workspace this app can optionally hand jobs to ([setup](docs/ODYSSEUS.md)). |

**Documentation:** [Odysseus integration](docs/ODYSSEUS.md) · [VRChat control](docs/VRCHAT.md) · [Browser extension](extension/README.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)

## Notices

Speech, voice and language models are downloaded only when you tick them and keep their own licences; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No analytics or tracking, and your audio is never saved.
