"""python -m voice_assistant [run|--setup|--selftest [file]|doctor|setup|fetch-models [kokoro] [--yes]|phone [pair [name]|list|unpair <id>]]"""
from __future__ import annotations

import sys
from pathlib import Path

from .config import load_config


def doctor() -> int:
    import httpx

    from .models import MODEL_SPECS

    cfg = load_config()
    print(f"Models dir: {cfg.models_dir}")
    for spec in MODEL_SPECS:
        ok = (Path(cfg.models_dir) / spec.slot / "tokens.txt").exists()
        print(f"  [{'ok' if ok else 'missing'}] {spec.slot}: {spec.name} ({spec.note})")
    try:
        r = httpx.get(cfg.llm_base_url.rstrip("/") + "/models", timeout=3)
        print(f"LLM endpoint {cfg.llm_base_url}: HTTP {r.status_code}")
    except httpx.HTTPError as e:
        print(f"LLM endpoint {cfg.llm_base_url}: unreachable ({e.__class__.__name__}). Start LM Studio's server.")
    print(f"Approved folders: {cfg.approved_folders or 'none'}")
    from .hardware import detect_gpus, recommend

    gpus = detect_gpus()
    print("GPU:", f"{gpus[0].name}: " + recommend(gpus[0].vram_gb, cfg.vram_reserve_gb) if gpus else "none detected")
    return 0


def setup() -> int:
    """Guided first run: report what is missing, ask before installing, pick devices."""
    from .audio_io import list_devices
    from .config import save_config

    doctor()
    cfg = load_config()
    print("\nMissing models can be downloaded now (about 100 MB, from github.com/k2-fsa/sherpa-onnx releases).")
    if input("Download missing models? [y/N] ").strip().lower() == "y":
        from .models import MODEL_SPECS, fetch_model

        for spec in MODEL_SPECS:
            if not (Path(cfg.models_dir) / spec.slot / "tokens.txt").exists():
                print(f"Fetching {spec.name} ...")
                fetch_model(spec, Path(cfg.models_dir))
    for key, kind, label in (("input_device", "input", "microphone"), ("output_device", "output", "speaker")):
        names = list_devices(kind)
        print(f"\nChoose {label} (Enter keeps '{getattr(cfg, key) or 'system default'}'):")
        for i, n in enumerate(names, 1):
            print(f"  {i}. {n}")
        pick = input("> ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(names):
            setattr(cfg, key, names[int(pick) - 1])
    save_config(cfg)
    print("\nSaved. Start LM Studio's server, then run: python -m voice_assistant")
    return 0


def fetch_models(args: list[str]) -> int:
    from .models import MODEL_SPECS, fetch_model

    assume_yes = "--yes" in args
    names = [a for a in args if not a.startswith("--")]
    cfg = load_config()
    for spec in MODEL_SPECS:
        if (names and spec.slot not in names) or (not names and not spec.core):
            continue
        if not assume_yes and input(f"Download {spec.name} ({spec.note}) from {spec.url}? [y/N] ").strip().lower() != "y":
            continue
        print(f"Fetching {spec.name} ...")
        print("  ->", fetch_model(spec, Path(cfg.models_dir)))
    return 0


def phone(args: list[str]) -> int:
    """python -m voice_assistant phone pair [name] | list | unpair <id>: manage the phones allowed to connect (see remote.py)."""
    import json

    from .remote import PairedDevices, tailscale_ip

    devices, action = PairedDevices(), (args[0] if args else "list")
    if action == "pair":
        cfg = load_config()
        host = tailscale_ip()
        if not host:
            print("Tailscale is not running on this PC. Install it, sign in, then try again.")
            return 1
        device_id, token = devices.pair(" ".join(args[1:]) or "Phone")
        print("Give this to the phone app (it is shown once and cannot be looked up again):")
        print(json.dumps({"v": 1, "host": host, "port": cfg.remote_port, "token": token, "id": device_id}))
        print("Also turn on the phone link: set \"remote_enabled\": true in the settings file, then restart the assistant.")
        return 0
    if action == "unpair" and len(args) > 1:
        print("Removed." if devices.revoke(args[1]) else "No phone with that id.")
        return 0
    for d in devices.list():
        print(f"{d['id']}  {d['name']}")
    return 0


def main(argv: list[str]) -> int:
    from .quiet import hide_child_consoles

    hide_child_consoles()
    cmd = argv[0] if argv else "run"
    if cmd == "--selftest":
        from pathlib import Path

        from .selftest import run as selftest

        return selftest(Path(argv[1]) if len(argv) > 1 else None)
    if cmd == "doctor":
        return doctor()
    if cmd == "setup":
        return setup()
    if cmd == "fetch-models":
        return fetch_models(argv[1:])
    if cmd == "phone":
        return phone(argv[1:])
    from .app import main as run

    return run(argv if argv and argv[0].startswith("--") else [])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
