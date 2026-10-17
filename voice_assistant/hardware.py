"""GPU detection and VRAM-tier recommendations (Windows)."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Gpu:
    name: str
    vram_gb: float


def _from_nvidia_smi() -> list[Gpu]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        name, _, mib = line.rpartition(",")
        gpus.append(Gpu(name.strip(), round(float(mib) / 1024, 1)))
    return gpus


def _from_registry() -> list[Gpu]:
    """Display adapters with 64-bit VRAM sizes (Win32_VideoController caps at 4 GB)."""
    try:
        import winreg
    except ImportError:
        return []
    base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    gpus = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return []
    for i in range(32):
        try:
            with winreg.OpenKey(root, f"{i:04d}") as k:
                name = winreg.QueryValueEx(k, "DriverDesc")[0]
                raw = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0]
        except OSError:
            continue
        size = int.from_bytes(raw, "little") if isinstance(raw, (bytes, bytearray)) else int(raw)
        if size > 0:
            gpus.append(Gpu(name, round(size / 1024**3, 1)))
    return gpus


def detect_gpus() -> list[Gpu]:
    gpus = _from_nvidia_smi() or _from_registry()
    return sorted(gpus, key=lambda g: -g.vram_gb)


def tier(vram_gb: float) -> str:
    if vram_gb >= 20:
        return "20+ GB"
    if vram_gb >= 12:
        return "12-20 GB"
    if vram_gb >= 8:
        return "8-12 GB"
    return "under 8 GB"


def recommend(vram_gb: float, reserve_gb: float) -> str:
    """Plain-language guidance. Speech recognition and TTS run on the CPU, so the LLM gets the VRAM."""
    usable = max(vram_gb - reserve_gb, 0)
    if usable >= 18:
        size = "a 26-35B mixture-of-experts model, or a dense model up to ~14B with long context"
    elif usable >= 10:
        size = "a 12-14B model at 4-bit (e.g. a Gemma 12B QAT)"
    elif usable >= 6:
        size = "a 7-9B model at 4-bit"
    elif usable >= 3:
        size = "a 3-4B model at 4-bit"
    else:
        size = "a cloud endpoint or a 1-2B model; there is little VRAM left after the reserve"
    return (
        f"{tier(vram_gb)} tier, {vram_gb:g} GB total, {reserve_gb:g} GB kept free for games/other apps "
        f"-> about {usable:g} GB for the model: {size}. Keep reasoning off for voice."
    )
