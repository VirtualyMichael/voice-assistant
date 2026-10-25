"""User settings. Stored as JSON; API keys are referenced by env-var name, never stored."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

INTERRUPT_MODES = ("voice", "hotkey", "ignore")
LISTEN_MODES = ("push", "always")
ODYSSEUS_MODES = ("off", "assist", "only")
REMOTE_OUTPUTS = ("phone", "both", "pc")


def config_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "VoiceAssistant"


def default_models_dir() -> Path:
    return config_dir() / "models"


@dataclass
class Config:
    hotkey: str = "ctrl+alt+space"
    setup_done: bool = False  # the first-run wizard has been completed
    # voice: speaking or hotkey interrupts | hotkey: only hotkey | ignore: reply always finishes
    interrupt_mode: str = "hotkey"
    barge_in_min_chars: int = 8
    listen_mode: str = "push"  # push: hotkey/Talk/wake word starts a turn | always: mic is always open
    # Devices by name ("" = system default). Second input is mixed in; second output gets the same audio.
    input_device: str = ""
    input_device_2: str = ""
    output_device: str = ""
    output_device_2: str = ""
    # Send the active microphone (VR mic while VR is running) to the 2nd output, e.g. CABLE Input, so a game that
    # uses the cable as its mic hears you. Replaces Windows' "Listen to this device" - turn that off.
    mic_forward: bool = False
    wake_word_enabled: bool = False
    wake_phrase: str = "hey assistant"
    wake_threshold: float = 0.12  # lower = more sensitive, more false triggers
    voice_approval: bool = False  # confirmations are also read aloud and can be answered by saying yes or no
    profile_learning: bool = True  # the assistant may propose facts about you for the profile (you confirm each)
    custom_voice_dir: str = ""  # folder with your own sherpa-onnx VITS/Piper voice (model .onnx, tokens.txt, espeak-ng-data); empty = built-in voices
    video_quality: str = "normal"  # how closely videos are watched: normal (640 px, 5 MB) | mid (960 px, 10 MB) | high (1280 px, 50 MB) | custom (the numbers below)
    video_custom_edge: int = 800  # custom level: longest side of a frame, in pixels
    video_custom_mb: float = 8.0  # custom level: most data for all the frames of one video, in MB
    video_custom_fps: float = 2.0  # custom level: most frames per second
    video_custom_frames: int = 120  # custom level: most frames in one video
    video_use_audio: bool = True  # listen to a video's sound and add what is said to the timeline (a phone clip is then recorded with sound)
    video_identify_model: str = ""  # a stronger model used only for the close-ups and the overview of a video (empty = the main model); same server and provider
    video_detail_frames: int = 3  # extra high-resolution close-ups taken from a video and read literally (text, held objects); 0 turns them off
    video_overview: bool = True  # one more pass over the notes to work out what the video is: the game, activity or event, how it is going
    video_research: bool = True  # let that pass look things up online to name a game or activity (needs web access on)
    video_skip_still: bool = False  # skip frames where the picture did not change (faster, but the model sees less); the skipped stretches are reported
    deep_vision: bool = True  # research pictures online (needs web access on): dropped photos and phone photos get a deeper, checked answer
    plantnet_enabled: bool = False  # also send plant photos to Pl@ntNet (needs your own key; the photo leaves this PC)
    scheduled_tasks_enabled: bool = True  # run scheduled reminders and jobs while the app is open (the Tasks tab lists them)
    check_updates: bool = True  # look for a newer release on GitHub at start-up (it always asks before installing)
    last_update_check: float = 0.0  # when that last happened (unix time)
    delegate_enabled: bool = False  # hand coding tasks to Claude Code / Codex (their CLIs, your logins)
    delegate_cmd_claude: str = "claude"
    delegate_cmd_codex: str = "codex"
    delegate_allow_edits: bool = False  # false = read-only runs
    delegate_daily_requests: int = 10
    delegate_daily_usd: float = 2.0  # 0 = no dollar limit (Codex reports no cost, so only requests are limited there)
    delegate_cooldown_s: int = 30
    delegate_timeout_s: int = 600
    glass: str = "frosted"  # window backdrop: off | frosted | mica (Windows 11 only; solid elsewhere)
    glass_tint: int = 55  # 0-100: how solid the cards are over the glass
    accent: str = "#6c8cff"
    ytm_extension_id: str = ""  # the browser extension the bridge trusts (filled in by the first one that connects)
    ytm_extension: bool = True  # use the YouTube Music bridge browser extension when it is installed and connected
    ytm_browser: bool = True  # play YouTube Music through the assistant's own YouTube Music window (controlled through its web page)
    computer_control: bool = False  # experimental: click, type and press keys in app windows (asks per window)
    persona_jarvis: bool = False  # calm, dry-witted British butler-style personality (pair with the George or Lewis UK voice)
    # VR auto-switch: while a VR runtime process is running, use the VR mic/speaker instead (empty = keep normal)
    vr_autoswitch: bool = False
    vr_input_device: str = ""
    vr_output_device: str = ""
    vr_processes: list[str] = field(default_factory=lambda: ["vrserver.exe", "vrmonitor.exe"])
    vr_poll_s: float = 3.0
    listen_tone: bool = True  # chime when listening starts; your speech is only captured after it ends
    listen_delay_s: float = 0.15  # extra quiet gap after the chime
    end_of_speech_s: float = 1.0  # silence that ends your turn; raise it if you get cut off mid-sentence
    post_speech_guard_s: float = 0.3  # ignore the mic briefly after the assistant stops talking
    listen_timeout_s: float = 8.0
    follow_up: bool = True  # keep listening after a spoken reply
    follow_up_s: float = 8.0  # ...for this many seconds; 0 turns follow-up off
    ambient_min_words: int = 3  # when the mic opened by itself (follow-up window, always-listening), ignore sentences shorter than this unless they are a command like 'stop'; 0 = keep everything
    accurate_asr: bool = True  # re-check each sentence with the larger recognizer when it is installed
    models_dir: str = field(default_factory=lambda: str(default_models_dir()))
    tts_speed: float = 1.0
    tts_voice: str = ""  # voice id, e.g. "am_michael"; empty = the model's first voice
    tts_threads: int = 6
    chatterbox_launch: str = ""  # path to Start-Chatterbox-Server.bat: started automatically (hidden) when the server is not running, and stopped on exit
    chatterbox_url: str = ""  # e.g. "http://127.0.0.1:8123": speak with your cloned voice from a local Chatterbox server; empty = built-in voices
    tts_pronunciations: dict = field(default_factory=dict)  # extra word -> spoken form, e.g. {"Nguyen": "Win"}
    vram_reserve_gb: float = 4.0  # VRAM kept free for games and other workloads
    always_on_top: bool = False
    opacity: float = 1.0
    window_size: list[int] = field(default_factory=list)
    llm_base_url: str = "http://localhost:1234/v1"  # LM Studio default
    llm_model: str = "local-model"
    llm_extra_body: dict = field(default_factory=dict)  # e.g. {"reasoning_effort": "none"} to skip thinking
    llm_provider: str = "local"  # local | ollama | openai | anthropic | custom
    ollama_base_url: str = "http://localhost:11434/v1"  # Ollama's OpenAI-compatible endpoint
    ollama_model: str = ""  # e.g. "llama3.2"
    custom_base_url: str = ""  # any OpenAI-compatible server (vLLM, OpenRouter, Groq, Together, a LAN box...)
    custom_model: str = ""
    custom_key_env: str = "CUSTOM_API_KEY"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = ""  # e.g. a current GPT model id
    openai_key_env: str = "OPENAI_API_KEY"
    anthropic_model: str = "claude-haiku-5-5"  # fast and cheap; claude-sonnet-5-5 / claude-opus-5-5 are stronger
    anthropic_key_env: str = "ANTHROPIC_API_KEY"
    cloud_ack: bool = False  # user has accepted that cloud providers receive the conversation
    mcp_servers: dict = field(default_factory=dict)  # see mcp_client.py
    odysseus_mode: str = "off"  # off: assistant alone | assist: also hands big jobs to Odysseus | only: voice only, every action goes through Odysseus
    odysseus_url: str = "http://127.0.0.1:7000"  # your Odysseus server; the API token is kept in Windows Credential Manager
    odysseus_endpoint_id: str = ""  # a model endpoint id from your Odysseus (Settings > models)
    odysseus_model: str = ""  # a model name served by that endpoint
    odysseus_launch: str = ""  # optional .bat/.cmd/.ps1 that starts Odysseus; run (hidden) when the server is not answering
    odysseus_share_tools: bool = False  # let Odysseus use this app's tools through tools_proxy.py (consent prompts still appear here)
    odysseus_auto_approve: bool = False  # false = ask before each hand-off to Odysseus
    llm_api_key_env: str = ""  # e.g. "OPENAI_API_KEY" for cloud endpoints
    system_prompt: str = (
        "You are a concise voice assistant. Reply in short spoken sentences without "
        "markdown, lists or emoji. Use the file tools only when the user asks about files."
    )
    approved_folders: list[str] = field(default_factory=list)
    max_tool_rounds: int = 8
    skill_folders: list[str] = field(default_factory=list)  # folders holding SKILL.md skills (one skill, or a parent of skills)
    app_guides: bool = True  # also search the built-in guides for common apps (the 'app guides' folder next to the program)
    library_folders: list[str] = field(default_factory=list)  # folders of reference documents the assistant can search (library.py); each folder is a collection
    disabled_skills: list[str] = field(default_factory=list)
    remember_conversation: bool = True  # every chat is saved on this PC only (the chats folder) and listed in the history sidebar; untick to stop saving
    history_messages: int = 40  # how many recent messages are kept and sent to the model
    allow_web: bool = False  # web_search / fetch_page send queries and URLs to the internet
    allow_shell: bool = False  # run_powershell (still asks before every command)
    confirm_launch: bool = True  # ask before opening an app (turn off to open apps instantly)
    app_shortcuts: dict = field(default_factory=lambda: {
        "notepad": "notepad.exe", "calculator": "calc.exe", "file explorer": "explorer.exe", "task manager": "taskmgr.exe",
    })
    govee_enabled: bool = False  # Govee lights over the local network (LAN Control must be on in the Govee Home app)
    light_names: dict = field(default_factory=dict)  # light ip -> friendly name, e.g. {"10.0.0.21": "desk lamp"}
    ha_url: str = ""  # Home Assistant address, e.g. http://homeassistant.local:8123 (token is in Credential Manager)
    duck_music: bool = False  # lower the music apps' own volume while the assistant listens, thinks and speaks
    duck_level: float = 0.25  # how loud the music stays while ducked (fraction of its level)
    vrchat_enabled: bool = False  # chatbox, avatar switches and a few controls over VRChat's OSC (Action Menu > Options > OSC > Enabled in the game)
    vrchat_host: str = "127.0.0.1"
    vrchat_port: int = 9000
    routines: dict = field(default_factory=dict)  # e.g. {"good night": [{"tool": "set_lights", "args": {"on": false}}, {"tool": "set_volume", "args": {"action": "mute"}}]}
    confirm_routines: bool = True
    main_language: str = "English"  # translations and screen reading answer in this language
    confirm_links: bool = True  # ask before opening a web link (turn off only if you trust the pages and files the assistant reads)
    confirm_clipboard: bool = True  # ask before reading or replacing your clipboard
    confirm_screen: bool = True  # ask before every screenshot (turn off only if you trust your AI provider)
    show_thinking: bool = True  # show the model's reasoning in a folded block above its answer (PC chat and phone); never spoken
    tool_filler: str = "One moment."  # spoken when a task starts before the reply; empty = silent
    vr_announce: bool = True  # speak a short confirmation when the VR profile switches
    remote_enabled: bool = False  # let a paired phone talk to this PC over Tailscale (see remote.py)
    remote_port: int = 47830
    remote_output: str = "phone"  # phone: replies play on the phone while it is connected | both | pc
    remote_allow_safety_changes: bool = False  # the owner's switch: lets a paired phone change safety settings (shell, web, confirmations...). Never settable from the phone.

    def validate(self) -> None:
        if self.interrupt_mode not in INTERRUPT_MODES:
            raise ValueError(f"interrupt_mode must be one of {INTERRUPT_MODES}")
        if self.listen_mode not in LISTEN_MODES:
            raise ValueError(f"listen_mode must be one of {LISTEN_MODES}")
        if self.odysseus_mode not in ODYSSEUS_MODES:
            raise ValueError(f"odysseus_mode must be one of {ODYSSEUS_MODES}")
        if self.remote_output not in REMOTE_OUTPUTS:
            raise ValueError(f"remote_output must be one of {REMOTE_OUTPUTS}")

    def device(self, key: str, vr: bool = False) -> str:
        """Device name for `key` (input_device / output_device), honouring the VR profile when vr is True."""
        return (getattr(self, "vr_" + key, "") if vr else "") or getattr(self, key)

    def api_key(self) -> str:
        return os.environ.get(self.llm_api_key_env, "") if self.llm_api_key_env else ""

    @property
    def is_cloud(self) -> bool:
        from urllib.parse import urlparse

        if self.llm_provider in ("openai", "anthropic"):
            return True
        url = {"ollama": self.ollama_base_url, "custom": self.custom_base_url}.get(self.llm_provider, self.llm_base_url)
        host = (urlparse(url).hostname or "").lower()
        return host not in ("localhost", "127.0.0.1", "::1")


def load_config(path: Path | None = None) -> Config:
    path = path or config_dir() / "config.json"
    cfg = Config()
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Config)}
        for k, v in data.items():
            if k in known:
                setattr(cfg, k, v)
    cfg.validate()
    return cfg


def save_config(cfg: Config, path: Path | None = None) -> None:
    path = path or config_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2), encoding="utf-8")
