"""Update check and installer download from the project's GitHub releases.

Only the public release page is contacted (no analytics, nothing about you is sent). An update is installed only after
the user agrees, and only if the downloaded installer matches the SHA-256 published next to it in the same release.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from . import __version__

REPO = "VirtualyMichael/voice-assistant"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALLER_RE = re.compile(r"^VoiceAssistant-Setup-(\d+(?:\.\d+)*)\.exe$")
HOST_SUFFIXES = ("github.com", "githubusercontent.com")
MAX_INSTALLER_BYTES = 400 * 1024 * 1024


class UpdateError(Exception):
    pass


@dataclass
class Release:
    version: str
    installer_name: str
    installer_url: str
    sha256_url: str
    notes: str
    page_url: str


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text.split("-")[0])
    return tuple(int(n) for n in nums) or (0,)


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _trusted(url: str) -> bool:
    u = httpx.URL(url)
    return u.scheme == "https" and any(u.host == s or u.host.endswith("." + s) for s in HOST_SUFFIXES)


def latest_release(client: httpx.Client | None = None) -> Release | None:
    """The newest published release that carries an installer and its checksum, or None."""
    own = client is None
    client = client or httpx.Client(timeout=15, follow_redirects=True, headers={"Accept": "application/vnd.github+json"})
    try:
        r = client.get(API_LATEST)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise UpdateError(f"could not check for updates ({e.__class__.__name__})") from e
    finally:
        if own:
            client.close()
    assets = {a.get("name", ""): a.get("browser_download_url", "") for a in data.get("assets", [])}
    for name, url in assets.items():
        m = INSTALLER_RE.match(name)
        sha = assets.get(name + ".sha256", "")
        if m and sha and _trusted(url) and _trusted(sha):
            return Release(m.group(1), name, url, sha, str(data.get("body") or ""), str(data.get("html_url") or f"https://github.com/{REPO}/releases"))
    return None


def check(current: str = __version__, client: httpx.Client | None = None) -> Release | None:
    """The release to offer, or None when this version is already the newest."""
    rel = latest_release(client)
    return rel if rel and is_newer(rel.version, current) else None


def can_self_update() -> str:
    """'' when this install can update itself, else why it cannot (running from source, or installed for all users)."""
    if not getattr(sys, "frozen", False):
        return "running from source: update with git pull"
    folder = Path(sys.executable).parent
    pf = [os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)")]
    if any(p and str(folder).lower().startswith(p.lower()) for p in pf) or not os.access(folder, os.W_OK):
        return "installed for all users: download the new installer from the release page"
    return ""


def download(rel: Release, dest_dir: Path, progress: Callable[[float], None] | None = None) -> Path:
    """Download the installer and verify it against the release's published SHA-256. Raises UpdateError on any mismatch."""
    if not INSTALLER_RE.match(rel.installer_name) or not (_trusted(rel.installer_url) and _trusted(rel.sha256_url)):
        raise UpdateError("the release has an unexpected file name or address")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / rel.installer_name
    part = target.with_suffix(".part")
    try:
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            sha_text = c.get(rel.sha256_url)
            sha_text.raise_for_status()
            m = re.search(r"\b[0-9a-fA-F]{64}\b", sha_text.text)
            if not m:
                raise UpdateError("the release has no readable checksum")
            expected = m.group(0).lower()
            digest, got = hashlib.sha256(), 0
            with c.stream("GET", rel.installer_url) as r:
                r.raise_for_status()
                if not _trusted(str(r.url)):
                    raise UpdateError("the download was redirected to an untrusted address")
                total = int(r.headers.get("content-length") or 0)
                with part.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        got += len(chunk)
                        if got > MAX_INSTALLER_BYTES:
                            raise UpdateError("the download is larger than expected")
                        digest.update(chunk)
                        f.write(chunk)
                        if progress and total:
                            progress(min(got / total, 1.0))
        if digest.hexdigest() != expected:
            raise UpdateError("the downloaded installer does not match its published checksum")
        part.replace(target)
        return target
    except httpx.HTTPError as e:
        raise UpdateError(f"download failed ({e.__class__.__name__})") from e
    finally:
        part.unlink(missing_ok=True)


def launch_installer(path: Path) -> None:
    """Start the installer detached; the app must exit right after. It reinstalls over this copy and restarts the app."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(  # noqa: S603 - a file we just verified against the release checksum
        [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/RELAUNCH=1"],
        creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
