# itch.io listing: paste-ready

Create the project at <https://itch.io/game/new> (the form says "game" but tools are allowed). Everything below maps to a field.

| Field | Enter |
|---|---|
| **Title** | Voice Assistant |
| **Project URL** | `voice-assistant` (pick something unique, e.g. `yourname.itch.io/voice-assistant`) |
| **Short description / tagline** (max ~100 chars) | Hands-free voice assistant for Windows: local AI, VR game voice, open apps, search files, add skills. |
| **Classification** | Tools (if offered; otherwise "Other") |
| **Kind of project** | Downloadable |
| **Release status** | Early access / "In development" (it is a 0.1.x release) |
| **Pricing** | Paid or pay-what-you-want. Suggested: **$3 minimum** (see the fee table in the chat), suggested donation $5. Free download with tips is the alternative. |
| **Genre / Tags** | Tool, Voice Control, Artificial Intelligence, Virtual Reality, Utilities, Windows, Offline, Productivity |
| **Uploads** | `VoiceAssistant-Setup-<version>.exe` from `dist\installer\` (tick **Windows**). |
| **Generative AI disclosure** | Answer **Yes** honestly: Text & dialog (the assistant's replies come from an AI model you connect), Sound (voices are synthetic speech), Code (the program was written with AI assistance). A "No AI" tag would be false. |
| **Community** | Comments on (so you can see bug reports) |
| **Cover image** | `store/cover-630x500.png` |
| **Screenshots** | `store/screenshots/1-main.png`, then `2-settings-0-voice.png`, `2-settings-2-vr.png`, `2-settings-4-skills.png`, `2-settings-5-apps.png` |
| **Banner / header (optional)** | `store/banner-1280x720.png` |
| **Refund policy** | Your choice; state it in the description (e.g. "Not happy? Message me within 14 days.") |

## Description (paste into the big text box)

**Talk to your PC. Keep your hands on the game.**

Voice Assistant is a hands-free voice assistant for Windows 10/11 that runs on your own computer. Press a hotkey or say your wake word, speak naturally, and it answers out loud while the text streams on screen.

**What it can do**
- Open any installed app by name ("open YouTube Music"), set timers, read what's on your clipboard (asks first)
- Search the web and read pages (off until you switch it on), find and read files in folders you approve, search and read PDFs
- Add **skills**: drop in a folder with a `SKILL.md` (the Bionic/Claude format), for example a mechanic helper that searches your owner's manuals
- Connect extra tool servers (MCP)
- Remembers your conversation between sessions (stored only on your PC; one click erases it)

**Made for gaming and VR**
- Pin the window on top of your game
- Use two microphones and two speakers; send your mic and the assistant's voice into a virtual audio cable so friends in VRChat hear them (needs the free [VB-Audio Virtual Cable](https://vb-audio.com/Cable/), installed separately)
- Switches to your headset mic and speakers automatically when SteamVR starts, and back when it closes
- Warns you if Windows' "Listen to this device" would make you heard twice

**Your choice of brain**
- Local: LM Studio, Ollama or llama.cpp (free, private, runs on your GPU)
- Cloud: Claude or OpenAI with your own API key (stored in Windows Credential Manager; the cloud company receives what you say)

**Speech**
- Natural voices (male and female) and accurate recognition run on your PC after a one-time download that the setup guide asks permission for (about 430 MB, or 900 MB with the accurate recognizer)

**Please read**
- You need an AI model source: install LM Studio (free) or enter a Claude/OpenAI key. The setup guide helps.
- Windows only. Uses about 1.3 GB of memory with all speech models loaded.
- The installer is not code-signed yet, so Windows SmartScreen shows "Unknown publisher": click *More info*, then *Run anyway*.
- It is an early release. Bug reports in the comments are welcome.
- Not affiliated with VRChat, Valve, LM Studio, Anthropic or OpenAI.

## System requirements (itch has a field for this)
Windows 10 (1809) or 11, 64-bit; 8 GB RAM minimum (16 GB recommended); about 2 GB free disk for models; a microphone; a GPU is only needed for the local AI model.

## After publishing
Set the page to **Draft** first, install it from itch on a second PC, and only then switch to **Public**.
