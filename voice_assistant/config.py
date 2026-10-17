"""User settings. Stored as JSON; API keys are referenced by env-var name, never stored."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

INTERRUPT_MODES = ("voice", "hotkey", "ignore")
LISTEN_MODES = ("push", "always")


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
    wake_threshold: float = 0.25  # lower = more sensitive, more false triggers
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
    accurate_asr: bool = True  # re-check each sentence with the larger recognizer when it is installed
    models_dir: str = field(default_factory=lambda: str(default_models_dir()))
    tts_speed: float = 1.0
    tts_voice: str = ""  # voice id, e.g. "am_michael"; empty = the model's first voice
    tts_threads: int = 6
    tts_pronunciations: dict = field(default_factory=dict)  # extra word -> spoken form, e.g. {"Gibson": "Gibb son"}
    vram_reserve_gb: float = 4.0  # VRAM kept free for games and other workloads
    always_on_top: bool = False
    opacity: float = 1.0
    window_size: list[int] = field(default_factory=list)
    llm_base_url: str = "http://localhost:1234/v1"  # LM Studio default
    llm_model: str = "local-model"
    llm_extra_body: dict = field(default_factory=dict)  # e.g. {"reasoning_effort": "none"} to skip thinking
    llm_provider: str = "local"  # local | openai | anthropic
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = ""  # e.g. a current GPT model id
    openai_key_env: str = "OPENAI_API_KEY"
    anthropic_model: str = "claude-haiku-5-5"  # fast and cheap; claude-sonnet-5-5 / claude-opus-5-5 are stronger
    anthropic_key_env: str = "ANTHROPIC_API_KEY"
    cloud_ack: bool = False  # user has accepted that cloud providers receive the conversation
    mcp_servers: dict = field(default_factory=dict)  # see mcp_client.py
    llm_api_key_env: str = ""  # e.g. "OPENAI_API_KEY" for cloud endpoints
    system_prompt: str = (
        "You are a concise voice assistant. Reply in short spoken sentences without "
        "markdown, lists or emoji. Use the file tools only when the user asks about files."
    )
    approved_folders: list[str] = field(default_factory=list)
    max_tool_rounds: int = 8
    skill_folders: list[str] = field(default_factory=list)  # folders holding SKILL.md skills (one skill, or a parent of skills)
    disabled_skills: list[str] = field(default_factory=list)
    remember_conversation: bool = True  # saved on this PC only (conversation.json); "New chat" or untick to erase
    history_messages: int = 40  # how many recent messages are kept and sent to the model
    allow_web: bool = False  # web_search / fetch_page send queries and URLs to the internet
    allow_shell: bool = False  # run_powershell (still asks before every command)
    confirm_launch: bool = True  # ask before opening an app (turn off to open apps instantly)
    app_shortcuts: dict = field(default_factory=lambda: {
        "notepad": "notepad.exe", "calculator": "calc.exe", "file explorer": "explorer.exe", "task manager": "taskmgr.exe",
    })
    tool_filler: str = "One moment."  # spoken when a task starts before the reply; empty = silent
    vr_announce: bool = True  # speak a short confirmation when the VR profile switches

    def validate(self) -> None:
        if self.interrupt_mode not in INTERRUPT_MODES:
            raise ValueError(f"interrupt_mode must be one of {INTERRUPT_MODES}")
        if self.listen_mode not in LISTEN_MODES:
            raise ValueError(f"listen_mode must be one of {LISTEN_MODES}")

    def device(self, key: str, vr: bool = False) -> str:
        """Device name for `key` (input_device / output_device), honouring the VR profile when vr is True."""
        return (getattr(self, "vr_" + key, "") if vr else "") or getattr(self, key)

    def api_key(self) -> str:
        return os.environ.get(self.llm_api_key_env, "") if self.llm_api_key_env else ""

    @property
    def is_cloud(self) -> bool:
        from urllib.parse import urlparse

        if self.llm_provider != "local":
            return True
        host = (urlparse(self.llm_base_url).hostname or "").lower()
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
