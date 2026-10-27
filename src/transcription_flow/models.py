"""Model manifest and downloader.

We fetch the CTranslate2 Whisper files ourselves instead of using
huggingface_hub: a pinned revision, a SHA-256 per file, resume with a Range
header, a progress callback, and no telemetry or revision checks. Once a
model is installed and verified, starting the app makes no network calls.
"""

import hashlib
import http.client
import json
import logging
import os
import shutil
import urllib.error
import urllib.request
from importlib import resources
from pathlib import Path

from . import REPO_URL, __version__

log = logging.getLogger(__name__)

DEFAULT_MODEL = "small.en"
DEFAULT_BASE_URL = "https://huggingface.co"
USER_AGENT = f"TranscriptionFlow/{__version__} (+{REPO_URL})"
MARKER = ".verified.json"
CHUNK = 1 << 20
DISK_MARGIN = 50 << 20


class ModelError(Exception):
    """A model problem; ``str(err)`` is a message fit for the user."""


class DownloadError(ModelError):
    pass


class ChecksumError(ModelError):
    pass


def load_manifest() -> dict:
    text = resources.files(__package__).joinpath("models_manifest.json").read_text("utf-8")
    return json.loads(text)


def _spec(name: str, manifest: dict) -> dict:
    try:
        return manifest["models"][name]
    except KeyError:
        raise ModelError(f"Unknown model {name!r}.") from None


def model_path(name: str, models_dir: Path) -> Path:
    return Path(models_dir) / name


def _marker_for(spec: dict) -> dict:
    return {"revision": spec["revision"],
            "files": {f["name"]: f["sha256"] for f in spec["files"]}}


def is_installed(name: str, models_dir: Path, manifest=None) -> bool:
    """True when the folder holds every file at its manifest size and a matching
    verification marker. Cheap: no hashing, no network."""
    spec = _spec(name, manifest or load_manifest())
    folder = model_path(name, models_dir)
    try:
        marker = json.loads((folder / MARKER).read_text("utf-8"))
    except (OSError, ValueError):
        return False
    if marker != _marker_for(spec):
        return False
    for f in spec["files"]:
        path = folder / f["name"]
        if not path.is_file() or path.stat().st_size != f["size"]:
            return False
    return True


def _sha256(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _fetch(url: str, part: Path, size: int, timeout: float, on_bytes) -> None:
    """Download url into part, resuming from part's current length."""
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    if have == size:
        return
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if have:
        request.add_header("Range", f"bytes={have}-")
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as err:
        if err.code == 416 and have:  # range not satisfiable: start over
            part.unlink()
            return _fetch(url, part, size, timeout, on_bytes)
        raise DownloadError(f"The download failed (HTTP {err.code}).") from None
    except (OSError, http.client.HTTPException) as err:  # URLError is an OSError
        raise DownloadError(f"The download failed: {_reason(err)}.") from None
    with response:
        if have and response.status != 206:  # server ignored Range: start over
            log.info("server ignored the range request; restarting %s", part.name)
            on_bytes(-have)
            have = 0
        try:
            with open(part, "ab" if have else "wb") as out:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    on_bytes(len(block))
        except (OSError, http.client.HTTPException) as err:
            raise DownloadError(f"The download was interrupted: {_reason(err)}.") from None


def _reason(err: Exception) -> str:
    reason = getattr(err, "reason", None) or err
    return str(reason) or type(err).__name__


def download(name: str, models_dir: Path, progress=None, base_url: str = DEFAULT_BASE_URL,
             timeout: float = 30.0, manifest=None) -> Path:
    """Download and verify a model; returns its folder.

    progress(done_bytes, total_bytes) is called as bytes arrive (also with the
    bytes already on disk). Partial files are kept as ``<name>.part`` so a
    later call resumes them. Raises DownloadError or ChecksumError.
    """
    manifest = manifest or load_manifest()
    spec = _spec(name, manifest)
    folder = model_path(name, models_dir)
    if is_installed(name, models_dir, manifest):
        return folder
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MARKER).unlink(missing_ok=True)

    total = sum(f["size"] for f in spec["files"])
    on_disk = 0
    for f in spec["files"]:
        for candidate in (folder / f["name"], folder / (f["name"] + ".part")):
            if candidate.is_file():
                on_disk += min(candidate.stat().st_size, f["size"])
                break
    free = shutil.disk_usage(folder).free
    if free < total - on_disk + DISK_MARGIN:
        raise DownloadError(
            f"Not enough disk space: the model needs {(total - on_disk) >> 20} MB more.")

    done = 0

    def on_bytes(count):
        nonlocal done
        done += count
        if progress:
            progress(done, total)

    for f in spec["files"]:
        final = folder / f["name"]
        if final.is_file() and final.stat().st_size == f["size"] and _sha256(final) == f["sha256"]:
            on_bytes(f["size"])
            continue
        final.unlink(missing_ok=True)
        part = folder / (f["name"] + ".part")
        if part.exists():
            on_bytes(min(part.stat().st_size, f["size"]))
        url = f"{base_url.rstrip('/')}/{spec['repo']}/resolve/{spec['revision']}/{f['name']}"
        _fetch(url, part, f["size"], timeout, on_bytes)
        actual_size = part.stat().st_size if part.exists() else 0
        if actual_size < f["size"]:
            raise DownloadError("The download was interrupted. Try again to resume it.")
        if actual_size != f["size"] or _sha256(part) != f["sha256"]:
            part.unlink()
            raise ChecksumError(
                f"{f['name']} did not match its checksum and was deleted. Please try again.")
        os.replace(part, final)
        log.info("downloaded %s/%s (%d bytes)", name, f["name"], f["size"])

    tmp = folder / (MARKER + ".tmp")
    tmp.write_text(json.dumps(_marker_for(spec)), "utf-8")
    os.replace(tmp, folder / MARKER)
    return folder
