"""ModelService: startup choice, download with cancel, load and re-check."""

import threading

import pytest

from transcription_flow import models
from transcription_flow.model_service import Cancelled, ModelService

NAMES = ("tiny.en", "base.en", "small.en")
MANIFEST = {"version": 1, "models": {
    name: {"repo": f"x/{name}", "revision": "r" * 40, "license": "MIT",
           "files": [{"name": "model.bin", "size": 10 * (NAMES.index(name) + 1),
                      "sha256": "0" * 64}]}
    for name in reversed(NAMES)}}  # listed largest first on purpose


def install(service, name, manifest=MANIFEST):
    import json

    spec = manifest["models"][name]
    folder = models.model_path(name, service.folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.bin").write_bytes(b"x" * spec["files"][0]["size"])
    (folder / models.MARKER).write_text(json.dumps(models._marker_for(spec)), "utf-8")


class FakeEngine:
    loads = []

    def __init__(self, folder, device, language):
        self.folder, self.device, self.language = folder, device, language

    def load(self):
        FakeEngine.loads.append(self)
        if self.folder.name == "small.en" and getattr(FakeEngine, "broken", False):
            raise RuntimeError("bad file")
        return self


@pytest.fixture
def service(tmp_path):
    FakeEngine.loads, FakeEngine.broken = [], False
    return ModelService(tmp_path / "models", manifest=MANIFEST, engine_factory=FakeEngine)


def test_names_are_smallest_first_and_smaller_than_walks_down(service):
    assert service.names() == ["tiny.en", "base.en", "small.en"]  # by size, not file order
    assert service.smaller_than("small.en") == "base.en"
    assert service.smaller_than("tiny.en") is None


@pytest.mark.parametrize("have, wanted, chosen", [
    ({"base.en", "small.en"}, "base.en", "base.en"),  # the choice itself
    ({"tiny.en", "small.en"}, "base.en", "small.en"),  # else the default
    ({"tiny.en", "base.en"}, "small.en", "base.en"),  # else the largest
    (set(), "small.en", None),  # nothing installed
])
def test_startup_choice(service, have, wanted, chosen):
    for name in have:
        install(service, name)
    assert service.startup_choice(wanted) == chosen


def test_startup_prefers_the_default_over_a_larger_installed_model(tmp_path):
    """With a model bigger than the default installed, the default still wins."""
    medium = {**MANIFEST["models"]["small.en"], "repo": "x/medium.en",
              "files": [{"name": "model.bin", "size": 99, "sha256": "0" * 64}]}
    manifest = {"version": 1, "models": {**MANIFEST["models"], "medium.en": medium}}
    service = ModelService(tmp_path / "models", manifest=manifest)
    for name in ("tiny.en", "small.en", "medium.en"):
        install(service, name, manifest)
    assert service.startup_choice("base.en") == models.DEFAULT_MODEL == "small.en"


def test_download_reports_progress_and_passes_the_manifest(service):
    seen = []

    def fake_download(name, folder, progress, verify, manifest):
        seen.append((name, folder, verify, manifest is MANIFEST))
        progress(5, 10)

    service._download = fake_download
    progress = []
    service.download("tiny.en", lambda d, t: progress.append((d, t)), threading.Event())
    assert seen == [("tiny.en", service.folder, False, True)] and progress == [(5, 10)]


def test_a_cancelled_download_stops_at_the_next_progress_step(service):
    cancel = threading.Event()

    def fake_download(name, folder, progress, verify, manifest):
        progress(1, 10)
        cancel.set()
        progress(2, 10)
        pytest.fail("kept going after the cancel")

    service._download = fake_download
    with pytest.raises(Cancelled):
        service.download("tiny.en", lambda d, t: None, cancel)


def test_load_maps_auto_to_none_and_uses_the_device(service):
    service.device = "cuda"
    engine = service.load("tiny.en", "auto")
    assert engine.language is None and engine.device == "cuda"
    assert engine.folder == service.folder / "tiny.en"
    assert service.load("tiny.en", "en").language == "en"


def test_a_model_that_failed_to_load_is_rechecked_before_the_next_try(service):
    install(service, "small.en")
    FakeEngine.broken = True
    with pytest.raises(RuntimeError):
        service.load("small.en", "en")
    assert service.needs_download("small.en")  # installed, but suspect
    verifies = []
    service._download = lambda name, folder, progress, verify, manifest: verifies.append(verify)
    service.download("small.en", lambda d, t: None, threading.Event())
    assert verifies == [True]
    FakeEngine.broken = False
    service.load("small.en", "en")
    assert not service.needs_download("small.en")


def test_the_real_downloader_is_looked_up_at_call_time(service, monkeypatch):
    calls = []
    monkeypatch.setattr(models, "download", lambda *a, **k: calls.append(a[0]))
    service._download = None
    service.download("base.en", lambda d, t: None, threading.Event())
    assert calls == ["base.en"]
