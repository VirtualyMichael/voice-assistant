"""The settings a paired phone can read and change.

Every field of Config appears exactly once, either in SETTINGS (shown on the phone with a plain-language label and help) or in PC_ONLY (with the
reason it is not); a test enforces that, so a setting added later cannot silently go missing from the phone.

* A change is checked against the setting's type and limits before it is applied, then saved like the PC window saves it.
* "live" settings take effect at once; "restart" settings are saved and take effect the next time the assistant starts.
* Sensitive settings (they widen what the assistant may do, turn off a question it asks first, or change where your conversation is sent) are
  locked unless the PC owner has ticked "Let my phone change safety settings" in the PC window (remote_allow_safety_changes, never
  changeable from the phone itself). The phone additionally asks for the owner's fingerprint or PIN before it sends such a change.
* Lists and tables (folders, MCP servers, routines...) are shown read-only for now; they are edited on the PC.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, fields
from typing import Any, Callable

from . import config as _config
from .config import Config

LIVE = "live"
RESTART = "restart"


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    help: str
    section: str
    kind: str  # toggle | choice | number | text | readonly
    choices: tuple[tuple[str, str], ...] = ()  # (value, label)
    device: str = ""  # "input" / "output": choices are this PC's audio devices
    allow_other: bool = False  # a choice that also accepts a typed value
    lo: float | None = None
    hi: float | None = None
    step: float | None = None
    integer: bool = False
    unit: str = ""
    multiline: bool = False
    maxlen: int = 200
    apply: str = RESTART
    sensitive: bool = False
    essential: bool = False


# (id, title, one-line description) in the order the phone lists them
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("phone", "Phone link", "How your phone and this PC work together."),
    ("voice", "Voice and listening", "When the assistant listens, and how it knows you have finished."),
    ("speech", "Speech", "How the assistant sounds."),
    ("ai", "AI model", "Which model answers, and in which language."),
    ("camera", "Camera and video", "How the assistant watches videos and looks at photos."),
    ("apps", "Apps and music", "Opening apps, and music while you talk."),
    ("home", "Smart home", "Lights and other devices."),
    ("memory", "Memory and privacy", "What the assistant remembers."),
    ("safety", "Safety and permissions", "What the assistant may do, and when it must ask first. Locked unless allowed on the PC."),
    ("delegate", "Coding helpers and Odysseus", "Hand bigger jobs to Claude Code, Codex or Odysseus."),
    ("vr", "VR and VRChat", "Headset audio and VRChat controls."),
    ("audio", "Audio devices on the PC", "Which microphone and speakers the PC itself uses."),
)


def _s(key, label, help, section, kind, **kw) -> Setting:
    return Setting(key, label, help, section, kind, **kw)


def toggle(key, label, help, section, **kw):
    return _s(key, label, help, section, "toggle", **kw)


def choice(key, label, help, section, options, **kw):
    return _s(key, label, help, section, "choice", choices=tuple(options), **kw)


def number(key, label, help, section, lo, hi, step, **kw):
    return _s(key, label, help, section, "number", lo=lo, hi=hi, step=step, **kw)


def text(key, label, help, section, **kw):
    return _s(key, label, help, section, "text", **kw)


_READONLY_SECTION = {
    "tts_pronunciations": "speech", "llm_extra_body": "ai", "app_shortcuts": "apps", "routines": "apps", "light_names": "home",
    "approved_folders": "safety", "skill_folders": "safety", "disabled_skills": "safety", "mcp_servers": "safety", "vr_processes": "vr",
}


def readonly(key, label, help, **kw):
    return _s(key, label, help, _READONLY_SECTION[key], "readonly", **kw)


LANGUAGES = tuple((n, n) for n in ("English", "Spanish", "French", "German", "Italian", "Portuguese", "Dutch", "Russian", "Japanese", "Korean", "Chinese", "Hindi", "Arabic"))
PROVIDERS = (("local", "LM Studio or llama.cpp on this PC"), ("ollama", "Ollama on this PC"), ("openai", "OpenAI (cloud)"), ("anthropic", "Claude (cloud)"), ("custom", "Another server"))

SETTINGS: tuple[Setting, ...] = (
    # ---- phone link ----
    choice("remote_output", "Where replies play", "Your phone while it is connected, the PC speakers, or both.", "phone",
           (("phone", "On my phone"), ("both", "Phone and PC"), ("pc", "On the PC")), apply=LIVE, essential=True),
    toggle("remote_enabled", "Phone link", "The connection that lets your phone talk to this PC. Turning it off here disconnects the phone for good until it is turned on at the PC.",
           "phone", sensitive=True),
    number("remote_port", "Phone link port", "The network port the phone connects to. Change it only if something else uses it; the phone must be paired again.", "phone",
           1024, 65535, 1, integer=True, sensitive=True),
    # ---- voice and listening ----
    choice("listen_mode", "Microphone", "When the PC's own microphone listens.", "voice",
           (("push", "Only when I start it"), ("always", "Always listening")), apply=LIVE),
    choice("interrupt_mode", "Talking over the assistant", "How you can cut its reply short at the PC.", "voice",
           (("voice", "Just start speaking"), ("hotkey", "Only with the hotkey or button"), ("ignore", "Never; let it finish")), apply=LIVE),
    number("barge_in_min_chars", "How much speech interrupts", "How many letters you must say before it stops talking. Higher means fewer accidental interruptions.", "voice",
           1, 40, 1, integer=True, apply=LIVE),
    toggle("listen_tone", "Chime when listening", "Plays a short sound at the PC when it starts listening. The phone shows its own cues instead.", "voice", apply=LIVE),
    number("listen_delay_s", "Pause after the chime", "A quiet gap so the chime is not mistaken for speech.", "voice", 0, 1, 0.05, unit="s", apply=LIVE),
    number("end_of_speech_s", "Pause that ends your sentence", "How long you can stay silent before the PC decides you finished. Raise it if you get cut off. (The phone has its own button or voice detection.)",
           "voice", 0.4, 3, 0.1, unit="s"),
    number("post_speech_guard_s", "Ignore the mic after it speaks", "Stops it hearing the end of its own voice.", "voice", 0, 1.5, 0.05, unit="s", apply=LIVE),
    number("listen_timeout_s", "How long it waits for you to start", "After listening starts, how long it waits for you to say something.", "voice", 2, 30, 1, unit="s", apply=LIVE),
    toggle("follow_up", "Keep listening after a reply", "Lets you answer without starting again. On the phone, hands-free uses the wake phrase instead.", "voice", apply=LIVE, essential=True),
    number("follow_up_s", "Follow-up time", "How long it keeps listening after a reply. 0 turns follow-up off.", "voice", 0, 30, 1, unit="s", apply=LIVE),
    toggle("accurate_asr", "Double-check each sentence", "Re-listens to every sentence with a bigger, more accurate model. A little slower, much more accurate.", "voice"),
    toggle("voice_approval", "Answer questions by voice", "Confirmations are also read aloud, and you can say yes or no.", "voice", apply=LIVE),
    toggle("wake_word_enabled", "Wake phrase on the PC", "Lets the PC's own microphone start listening when it hears the phrase. (Your phone has its own wake phrase in the app.)", "voice"),
    text("wake_phrase", "PC wake phrase", "The words that start listening at the PC, for example \"hey assistant\".", "voice", maxlen=40),
    number("wake_threshold", "PC wake sensitivity", "Lower hears you more easily but may start by mistake.", "voice", 0.03, 0.5, 0.01),
    text("tool_filler", "Phrase while it works", "Said when it starts a task before it has anything to say. Empty means silence.", "voice", maxlen=60, apply=LIVE),
    # ---- speech ----
    number("tts_speed", "Speaking speed", "1 is normal. Lower is slower, higher is faster.", "speech", 0.6, 1.6, 0.05, essential=True),
    text("tts_voice", "Voice", "The voice name, for example am_michael. Empty uses the model's first voice.", "speech", maxlen=60),
    number("tts_threads", "Speech processing threads", "How many processor threads create the voice. Higher is faster up to a point.", "speech", 1, 16, 1, integer=True),
    toggle("persona_jarvis", "Butler personality", "A calm, dry-witted British butler style. Pairs well with a British voice.", "speech", essential=True),
    text("custom_voice_dir", "Your own voice folder", "A folder with a Piper or VITS voice you have the rights to use.", "speech", maxlen=260),
    text("chatterbox_url", "Cloned voice server", "The address of your Chatterbox server, for example http://127.0.0.1:8123.", "speech", maxlen=200, sensitive=True),
    text("chatterbox_launch", "Cloned voice start file", "A .bat file that starts the cloned voice server. It is run when the assistant needs it.", "speech", maxlen=260, sensitive=True),
    readonly("tts_pronunciations", "Pronunciations", "Words with a spoken spelling, for example Nguyen said as Win. Edit these on the PC."),
    # ---- AI model ----
    choice("main_language", "Language for translations", "Translations and screen or photo reading answer in this language.", "ai", LANGUAGES, allow_other=True, apply=LIVE, essential=True),
    choice("llm_provider", "Where the AI runs", "Cloud choices send your conversation to that company.", "ai", PROVIDERS, sensitive=True),
    text("llm_model", "Model name", "The model to use on your local server, as it is named there.", "ai", maxlen=120, essential=True),
    text("llm_base_url", "Local server address", "The address of LM Studio or llama.cpp.", "ai", maxlen=200, sensitive=True),
    text("ollama_base_url", "Ollama address", "Ollama's OpenAI-compatible address.", "ai", maxlen=200, sensitive=True),
    text("ollama_model", "Ollama model", "For example llama3.2.", "ai", maxlen=120),
    text("custom_base_url", "Other server address", "Any OpenAI-compatible server, such as OpenRouter, Groq or one on your network.", "ai", maxlen=200, sensitive=True),
    text("custom_model", "Other server model", "The model name on that server.", "ai", maxlen=120),
    text("custom_key_env", "Other server key name", "The name of the PC setting that holds its key. The key itself is never shown or sent.", "ai", maxlen=80, sensitive=True),
    text("openai_base_url", "OpenAI address", "Change only for an OpenAI-compatible proxy.", "ai", maxlen=200, sensitive=True),
    text("openai_model", "OpenAI model", "The OpenAI model to use.", "ai", maxlen=120),
    text("openai_key_env", "OpenAI key name", "The name of the PC setting that holds the key. The key itself is never shown or sent.", "ai", maxlen=80, sensitive=True),
    text("anthropic_model", "Claude model", "A faster, cheaper model or a stronger one.", "ai", maxlen=120),
    text("anthropic_key_env", "Claude key name", "The name of the PC setting that holds the key. The key itself is never shown or sent.", "ai", maxlen=80, sensitive=True),
    text("llm_api_key_env", "Server key name", "For cloud endpoints: the name of the PC setting that holds the key.", "ai", maxlen=80, sensitive=True),
    number("max_tool_rounds", "Steps it may take per request", "How many tool steps it can chain before answering.", "ai", 1, 30, 1, integer=True),
    number("history_messages", "Conversation it remembers", "How many recent messages are sent to the model each time.", "ai", 4, 200, 2, integer=True),
    number("vram_reserve_gb", "Graphics memory kept free", "Memory left free for games and other programs when choosing a model.", "ai", 0, 24, 0.5, unit="GB"),
    readonly("llm_extra_body", "Extra model options", "Advanced options sent with every request. Edit these on the PC."),
    # ---- apps and music ----
    toggle("ytm_extension", "YouTube Music browser extension", "Plays music in your own signed-in YouTube Music tab when the extension is installed.", "apps"),
    toggle("ytm_browser", "Assistant's YouTube Music window", "Plays music in a window the assistant keeps, when the extension is not used.", "apps"),
    toggle("duck_music", "Lower music while it talks", "Turns music apps down while the assistant listens, thinks and speaks.", "apps", apply=LIVE),
    number("duck_level", "How quiet the music gets", "The fraction of normal volume the music keeps while lowered.", "apps", 0.05, 1, 0.05),
    readonly("app_shortcuts", "App shortcuts", "Names you can say to open apps. Edit these on the PC."),
    readonly("routines", "Routines", "Saved multi-step phrases such as \"good night\". Edit these on the PC."),
    # ---- smart home ----
    toggle("govee_enabled", "Govee lights", "Controls Govee lights on your network. LAN Control must be on in the Govee app.", "home"),
    text("ha_url", "Home Assistant address", "For example http://homeassistant.local:8123. The token stays on the PC.", "home", maxlen=200, sensitive=True),
    readonly("light_names", "Light names", "Friendly names for your lights. Edit these on the PC."),
    # ---- memory and privacy ----
    toggle("remember_conversation", "Remember the conversation", "Saves your chats on this PC so you can reopen them from the history list. Turning it off stops saving; saved chats stay until you delete them.", "memory"),
    toggle("profile_learning", "Learn facts about me", "The assistant may suggest facts for your profile, and you confirm each one.", "memory"),
    toggle("scheduled_tasks_enabled", "Run scheduled tasks", "Lets reminders and jobs you scheduled run at their time while the PC app is open.", "memory"),
    toggle("check_updates", "Check for updates", "Looks for a newer version when it starts. It always asks before installing.", "memory"),
    # ---- safety and permissions (all sensitive) ----
    toggle("allow_web", "Use the internet", "Lets it search and read web pages. Your questions and links leave this PC.", "safety", sensitive=True),
    choice("video_quality", "Video detail", "How closely it watches videos. Normal: 640 px, up to 5 MB of frames. Mid: 960 px, 10 MB. High: 1280 px, 50 MB (slower, more data from the phone). Custom: your own numbers below.", "camera",
           (("normal", "Normal"), ("mid", "Mid"), ("high", "High"), ("custom", "Custom"))),
    number("video_custom_edge", "Custom video: picture size", "The longest side of each frame, in pixels.", "camera", 320, 1920, 80, integer=True, unit="px"),
    number("video_custom_mb", "Custom video: data budget", "The most data for all the frames of one video. More data means more frames and more waiting.", "camera", 0.5, 60, 0.5, unit="MB"),
    number("video_custom_fps", "Custom video: frames per second", "The most frames taken each second of video.", "camera", 0.1, 10, 0.1, unit="/s"),
    number("video_custom_frames", "Custom video: most frames", "The most frames taken from one video.", "camera", 2, 500, 10, integer=True),
    toggle("video_use_audio", "Video sound", "Listens to a video's sound and adds what is said. A clip recorded on the phone then includes sound.", "camera", sensitive=True),
    text("video_identify_model", "Model for working out what a video is", "A stronger model for the close-ups and the overview only (same server). Leave empty to use the main model.", "camera", maxlen=120),
    number("video_detail_frames", "Video close-ups", "How many extra high-resolution frames are looked at very closely to read text and see what is held. 0 turns them off.", "camera", 0, 6, 1, integer=True),
    toggle("video_overview", "Work out what a video is", "A final pass that names the game, activity or event and how it is going.", "camera"),
    toggle("video_research", "Look things up for videos", "Lets that pass search the web to name a game or activity. Needs 'Use the internet'.", "camera", sensitive=True),
    toggle("video_skip_still", "Skip still frames", "Skip frames where nothing changed. Faster, but it sees less; skipped stretches are reported.", "camera"),
    toggle("deep_vision", "Research photos online", "Photos you drop in or take with your phone are studied closely and searched for online. Needs 'Use the internet'. Only short text searches go online, not the photo.", "camera", sensitive=True),
    toggle("allow_shell", "Run PowerShell commands", "Lets it run commands. It still asks before each one.", "safety", sensitive=True),
    toggle("computer_control", "Control app windows", "Experimental: click, type and press keys in app windows. It asks per window.", "safety", sensitive=True),
    toggle("confirm_launch", "Ask before opening an app", "Turn off to let it open apps without asking.", "safety", sensitive=True, apply=LIVE),
    toggle("confirm_links", "Ask before opening a link", "A hostile web page could try to make it open a link, so leave this on unless you trust what it reads.", "safety", sensitive=True, apply=LIVE),
    toggle("confirm_screen", "Ask before taking a screenshot", "A screenshot goes to your AI model. With a cloud model the picture leaves this PC.", "safety", sensitive=True, apply=LIVE),
    toggle("confirm_clipboard", "Ask before using the clipboard", "The clipboard may hold passwords or private text.", "safety", sensitive=True, apply=LIVE),
    toggle("confirm_routines", "Ask before running a routine", "Saving or deleting a routine always asks.", "safety", sensitive=True, apply=LIVE),
    toggle("cloud_ack", "I accept cloud providers see my conversation", "Needed before a cloud model is used.", "safety", sensitive=True),
    text("system_prompt", "Basic instructions", "The standing instructions the assistant follows. Changing them changes how it behaves.", "safety", multiline=True, maxlen=4000, sensitive=True),
    readonly("approved_folders", "Folders it may use", "Folders the assistant may read and write. Edit these on the PC."),
    readonly("skill_folders", "Skill folders", "Folders that hold extra skills. Edit these on the PC."),
    readonly("disabled_skills", "Skills turned off", "Skills you switched off. Edit these on the PC."),
    readonly("mcp_servers", "Tool servers (MCP)", "Extra tools from other programs. Edit these on the PC."),
    # ---- coding helpers and Odysseus ----
    toggle("delegate_enabled", "Hand coding jobs to Claude Code or Codex", "Uses their command-line tools and your logins.", "delegate", sensitive=True),
    toggle("delegate_allow_edits", "Let them change files", "Off means read-only runs.", "delegate", sensitive=True),
    text("delegate_cmd_claude", "Claude Code command", "The command that starts Claude Code.", "delegate", maxlen=200, sensitive=True),
    text("delegate_cmd_codex", "Codex command", "The command that starts Codex.", "delegate", maxlen=200, sensitive=True),
    number("delegate_daily_requests", "Jobs per day", "The most jobs it may hand over in a day.", "delegate", 1, 200, 1, integer=True),
    number("delegate_daily_usd", "Spending limit per day", "0 means no dollar limit.", "delegate", 0, 100, 0.5, unit="$"),
    number("delegate_cooldown_s", "Pause between jobs", "Minimum wait between handed-over jobs.", "delegate", 0, 600, 5, unit="s"),
    number("delegate_timeout_s", "Longest a job may run", "A job is stopped after this long.", "delegate", 30, 7200, 30, unit="s"),
    choice("odysseus_mode", "Odysseus", "Off: the assistant works alone. Assist: it also hands big jobs to Odysseus. Only: everything goes through Odysseus.", "delegate",
           (("off", "Off"), ("assist", "Assist"), ("only", "Voice only")), sensitive=True),
    text("odysseus_url", "Odysseus address", "Where your Odysseus server runs. Its token stays on the PC.", "delegate", maxlen=200, sensitive=True),
    text("odysseus_endpoint_id", "Odysseus model endpoint", "The model endpoint ID from your Odysseus.", "delegate", maxlen=80),
    text("odysseus_model", "Odysseus model", "The model name served by that endpoint.", "delegate", maxlen=120),
    text("odysseus_launch", "Odysseus start file", "A file that starts Odysseus. It is run when the server is not answering.", "delegate", maxlen=260, sensitive=True),
    toggle("odysseus_share_tools", "Let Odysseus use this assistant's tools", "Odysseus can then use your apps, timers, music and more. Confirmations still appear.", "delegate", sensitive=True),
    toggle("odysseus_auto_approve", "Hand jobs to Odysseus without asking", "Off means it asks before each hand-over.", "delegate", sensitive=True),
    # ---- VR ----
    toggle("vr_autoswitch", "Switch audio when SteamVR starts", "Moves the microphone and headset to your VR devices and back.", "vr"),
    number("vr_poll_s", "How often it checks for VR", "Seconds between checks.", "vr", 1, 30, 1, unit="s"),
    toggle("vr_announce", "Announce the VR switch", "Says a short confirmation when it switches.", "vr"),
    toggle("vrchat_enabled", "VRChat controls", "Chatbox, avatar switches and a few controls. Turn on OSC in VRChat's menu.", "vr"),
    text("vrchat_host", "VRChat address", "Usually this PC.", "vr", maxlen=100),
    number("vrchat_port", "VRChat port", "VRChat's OSC port, normally 9000.", "vr", 1024, 65535, 1, integer=True),
    _s("vr_input_device", "VR microphone", "The microphone used while VR is running.", "vr", "choice", device="input"),
    _s("vr_output_device", "VR speakers", "The speakers or headset used while VR is running.", "vr", "choice", device="output"),
    readonly("vr_processes", "Programs that count as VR", "Program names that mean VR is running. Edit these on the PC."),
    # ---- audio devices ----
    _s("input_device", "PC microphone", "The microphone the PC itself listens with.", "audio", "choice", device="input"),
    _s("input_device_2", "Second PC microphone", "Mixed into the first.", "audio", "choice", device="input"),
    _s("output_device", "PC speakers", "Where the PC plays the assistant's voice.", "audio", "choice", device="output"),
    _s("output_device_2", "Second PC output", "Gets the same audio, for example headphones or a virtual cable.", "audio", "choice", device="output"),
)

# Config fields that are not offered on the phone, and why.
PC_ONLY: dict[str, str] = {
    "hotkey": "a keyboard shortcut on the PC",
    "setup_done": "internal: the first-run guide has been completed",
    "last_update_check": "internal: when updates were last checked",
    "models_dir": "a folder path on the PC",
    "glass": "how the PC window looks",
    "glass_tint": "how the PC window looks",
    "accent": "how the PC window looks",
    "always_on_top": "how the PC window behaves",
    "opacity": "how the PC window looks",
    "window_size": "internal: the PC window's size",
    "ytm_extension_id": "internal: the browser extension the PC trusts",
    "mic_forward": "needs the PC's audio routing set up (virtual cable)",
    "plantnet_enabled": "sends plant photos to a third party and needs a key entered on the PC",
    "remote_allow_safety_changes": "the owner's switch on the PC; it must never be changeable from the phone",
}

# Settings read each time they are used, so a change takes effect at once. Everything else is read when the assistant starts.
LIVE_VERIFIED = frozenset({
    "listen_mode", "interrupt_mode", "barge_in_min_chars", "listen_tone", "listen_delay_s", "post_speech_guard_s", "listen_timeout_s",
    "follow_up", "follow_up_s", "tool_filler", "voice_approval", "main_language", "remote_output", "duck_music",
    "confirm_launch", "confirm_links", "confirm_screen", "confirm_clipboard", "confirm_routines",
})

BY_KEY: dict[str, Setting] = {s.key: s for s in SETTINGS}


class SettingError(ValueError):
    """A change that was refused; the message is plain language for the phone to show."""


def _dynamic_choices(setting: Setting, device_names: Callable[[str], list[str]] | None) -> list[tuple[str, str]]:
    if not setting.device:
        return list(setting.choices)
    try:
        names = list(device_names(setting.device)) if device_names else []
    except Exception:  # the audio system can be unavailable; the current value still works
        names = []
    return [("", "System default")] + [(n, n) for n in names]


def validate(setting: Setting, value: Any, device_names: Callable[[str], list[str]] | None = None) -> Any:
    """The value in the form Config stores, or SettingError."""
    kind = setting.kind
    if kind == "readonly":
        raise SettingError(f"{setting.label} is edited on the PC.")
    if kind == "toggle":
        if not isinstance(value, bool):
            raise SettingError("That should be on or off.")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingError("That should be a number.")
        if setting.integer:
            if isinstance(value, float) and not value.is_integer():
                raise SettingError("That should be a whole number.")
            value = int(value)
        else:
            value = float(value)
        if (setting.lo is not None and value < setting.lo) or (setting.hi is not None and value > setting.hi):
            raise SettingError(f"That should be between {setting.lo:g} and {setting.hi:g}.")
        return value
    if not isinstance(value, str):
        raise SettingError("That should be text.")
    if len(value) > setting.maxlen:
        raise SettingError(f"That is too long (the limit is {setting.maxlen} characters).")
    if kind == "choice":
        allowed = {v for v, _ in _dynamic_choices(setting, device_names)}
        if value not in allowed and not setting.allow_other:
            raise SettingError("That is not one of the choices.")
        if setting.allow_other and (not value.strip() or "\n" in value):
            raise SettingError("That should be a short word or two.")
        return value
    if not setting.multiline and ("\n" in value or "\r" in value):
        raise SettingError("That should be a single line.")
    return value


def _summary(value: Any) -> dict:
    if isinstance(value, dict):
        items = [f"{k}: {v}"[:80] for k, v in list(value.items())[:8]]
        return {"summary": f"{len(value)} item{'s' if len(value) != 1 else ''}" if value else "None", "items": items}
    if isinstance(value, (list, tuple)):
        return {"summary": f"{len(value)} item{'s' if len(value) != 1 else ''}" if value else "None", "items": [str(v)[:80] for v in list(value)[:8]]}
    return {"summary": str(value)[:80], "items": []}


class SettingsService:
    """Reads and changes the assistant's settings on behalf of a paired phone."""

    def __init__(self, cfg: Config, allow_safety: Callable[[], bool], device_names: Callable[[str], list[str]] | None = None, save=None):
        self.cfg, self.allow_safety, self.device_names = cfg, allow_safety, device_names
        self._save = save  # tests pass their own; by default the PC's real settings file
        self.restart_pending = False
        self._lock = threading.Lock()

    def _item(self, s: Setting, defaults: Config, allowed: bool) -> dict:
        value = getattr(self.cfg, s.key)
        item: dict = {
            "key": s.key, "label": s.label, "help": s.help, "kind": s.kind, "apply": s.apply, "sensitive": s.sensitive,
            "locked": s.sensitive and not allowed, "essential": s.essential,
        }
        if s.kind == "readonly":
            item.update(_summary(value))
            return item
        item["value"] = value
        item["default"] = getattr(defaults, s.key)
        if s.kind == "choice":
            choices = _dynamic_choices(s, self.device_names)
            if value not in {v for v, _ in choices}:
                choices.append((value, value or "System default"))
            item["choices"] = [{"value": v, "label": label} for v, label in choices]
            item["allow_other"] = s.allow_other
        elif s.kind == "number":
            item.update({"min": s.lo, "max": s.hi, "step": s.step, "integer": s.integer, "unit": s.unit})
        elif s.kind == "text":
            item.update({"multiline": s.multiline, "maxlen": s.maxlen})
        return item

    def snapshot(self) -> dict:
        allowed, defaults = bool(self.allow_safety()), Config()
        sections = []
        for sid, title, blurb in SECTIONS:
            items = [self._item(s, defaults, allowed) for s in SETTINGS if s.section == sid]
            if items:
                sections.append({"id": sid, "title": title, "blurb": blurb, "items": items})
        return {"allow_safety": allowed, "restart_pending": self.restart_pending, "sections": sections}

    def change(self, key: str, value: Any) -> dict:
        """Apply one change. Always returns a result dict; never raises for a refused change."""
        s = BY_KEY.get(key)
        try:
            if s is None:
                raise SettingError("That setting can only be changed on the PC.")
            if s.sensitive and not self.allow_safety():
                raise SettingError("This is a safety setting. Turn on \"Let my phone change safety settings\" in the PC window (General page) first.")
            value = validate(s, value, self.device_names)
        except SettingError as e:
            current = getattr(self.cfg, key, None) if key in BY_KEY else None
            return {"ok": False, "key": key, "value": current, "message": str(e), "restart": False}
        with self._lock:
            setattr(self.cfg, key, value)
            (self._save or _config.save_config)(self.cfg)
            restart = s.apply == RESTART
            if restart:
                self.restart_pending = True
        return {"ok": True, "key": key, "value": value, "restart": restart,
                "message": "Saved. It takes effect when the assistant restarts." if restart else "Saved."}


def coverage_problems() -> list[str]:
    """Empty when every Config field is on the phone or deliberately PC-only, exactly once."""
    problems = []
    names = {f.name for f in fields(Config)}
    for key in names - set(BY_KEY) - set(PC_ONLY):
        problems.append(f"{key}: not on the phone and not marked PC-only")
    for key in set(BY_KEY) - names:
        problems.append(f"{key}: listed for the phone but not a setting")
    for key in set(BY_KEY) & set(PC_ONLY):
        problems.append(f"{key}: both on the phone and PC-only")
    for key in set(PC_ONLY) - names:
        problems.append(f"{key}: marked PC-only but not a setting")
    return problems
