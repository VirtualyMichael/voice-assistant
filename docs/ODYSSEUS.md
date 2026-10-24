# Using Odysseus with the voice assistant (optional)

[Odysseus](https://github.com/odysseus-dev/odysseus) is a separate, self-hosted agent workspace (research, documents,
notes, email, calendar, memory, shell). The voice assistant can hand jobs to it. **This is off by default.** Nothing from
Odysseus is bundled or installed by this app; you install and run it yourself, and the app only talks to its HTTP API.

## Modes (Settings > AI > Odysseus, restart to apply)

| Mode | What happens |
|---|---|
| Voice assistant alone | Default. Odysseus is never contacted. |
| With Odysseus | The assistant keeps all its own tools and also gets `ask_odysseus` for bigger or multi-step jobs. |
| Voice only | The assistant is just the voice and reply system. Its own action tools are removed and every action goes through Odysseus. If Odysseus is unavailable it says so. |

## Setup
1. Install and start Odysseus following its own instructions, and create your admin account.
2. In Odysseus add a model endpoint (LM Studio, Ollama or a cloud key). Open `<your address>/api/model-endpoints` while
   signed in to see the endpoint **id**, and `<your address>/api/model-endpoints/<id>/models` for the **model** names.
3. Create an API token with only the **chat** scope.
4. In the voice assistant: set the address, endpoint id and model, then **Set Odysseus token...** (stored in Windows
   Credential Manager, never in the settings file) and press **Test the connection**.
5. Optional: **Choose start file...** points at a `.bat`, `.cmd` or `.ps1` that starts Odysseus. The app runs it hidden
   when the server is not answering and the assistant needs it.

## Long jobs, progress and background work

Hand-offs stream: while Odysseus works, short spoken progress lines ("Searching online.", "Checking your email.") are given at most once every 12 seconds, and each tool it starts appears as a note in the chat. If a job takes longer than about 25 seconds the conversation carries on and the result is announced when it finishes. The assistant can also start a job in the background on purpose, list jobs (`check_odysseus`) or stop one (`cancel_odysseus`). One job runs at a time, and if Odysseus asks a question the assistant relays it to you and sends your answer on.

## Letting Odysseus use this app's tools (optional)

In **Voice only** mode the assistant has no tools of its own, so Odysseus needs them. Tick **Let Odysseus use this app's
tools** (Settings > AI > Odysseus, restart to apply), then press **Show the Odysseus tool setup** and add the values it
shows in Odysseus under Settings > MCP: a **stdio** server running `tools_proxy.py` with any Python 3 (Odysseus's own
venv Python works). Odysseus then sees every tool this app has (apps, timers, music and volume, lights, VRChat, screen
help, files, and so on) except `ask_odysseus` itself.

- The proxy is a few lines of standard-library Python; it owns no tools and forwards to the running app over
  `127.0.0.1` with a token that is regenerated at every start and stored in `%APPDATA%\VoiceAssistant\tool-server.json`.
- Every consent prompt (opening apps, writing files, shell, controlling windows, hand-offs) still appears in this app's
  window, and tools run one at a time. If you are away from the screen, nothing that asks first will run.
- The app must be running with the option ticked; otherwise Odysseus's calls report that the app is not running.

## Safety
- Every hand-off asks first by default (untick *Ask before each hand-off* to skip). Odysseus can run shell commands and
  read your mail, so keep the token to the `chat` scope, keep Odysseus bound to `127.0.0.1`, and keep its login enabled.
- Odysseus replies are treated as data, not instructions.
- The text of each hand-off is sent to the address you configured. Point it only at a server you control.
- Odysseus is AGPL-3.0; this app does not include or link its code.
