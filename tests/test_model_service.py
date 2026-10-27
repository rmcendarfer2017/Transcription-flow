"""ModelService: startup choice, download with cancel, load and re-check."""

import threading
import time

import pytest

from helpers import wait_for
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


def test_a_model_that_failed_to_load_is_skipped_at_start_until_it_loads(service):
    """QA B2: the next start choice passes over it, and the toast can say why."""
    install(service, "tiny.en")
    install(service, "small.en")
    FakeEngine.broken = True
    with pytest.raises(RuntimeError):
        service.load("small.en", "en")
    assert service.failed_to_load("small.en") and not service.failed_to_load("tiny.en")
    assert service.startup_choice("small.en") == "tiny.en"
    FakeEngine.broken = False
    service.load("small.en", "en")
    assert not service.failed_to_load("small.en")
    assert service.startup_choice("small.en") == "small.en"


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

    def fake_download(name, folder, progress, verify, manifest, before_write):
        seen.append((name, folder, verify, manifest is MANIFEST))
        before_write()
        progress(5, 10)

    service._download = fake_download
    progress = []
    service.download("tiny.en", lambda d, t: progress.append((d, t)), threading.Event())
    assert seen == [("tiny.en", service.folder, False, True)] and progress == [(5, 10)]


def test_a_cancelled_download_stops_at_the_next_progress_step(service):
    cancel = threading.Event()

    def fake_download(name, folder, progress, verify, manifest, before_write):
        progress(1, 10)
        cancel.set()
        progress(2, 10)
        pytest.fail("kept going after the cancel")

    service._download = fake_download
    with pytest.raises(Cancelled):
        service.download("tiny.en", lambda d, t: None, cancel)


def test_a_cancel_stops_before_the_next_block_is_written(service):
    cancel = threading.Event()

    def fake_download(name, folder, progress, verify, manifest, before_write):
        before_write()  # not cancelled: the block may be written
        cancel.set()
        before_write()
        pytest.fail("wrote a block after the cancel")

    service._download = fake_download
    with pytest.raises(Cancelled):
        service.download("tiny.en", lambda d, t: None, cancel)


def test_a_download_cancelled_before_it_gets_the_lock_never_starts(service):
    """review-m3 X6: a job cancelled while it waited for the per-model lock
    must not open a connection when it gets the lock."""
    cancel = threading.Event()
    cancel.set()
    service._download = lambda *a, **k: pytest.fail("downloaded after the cancel")
    with pytest.raises(Cancelled):
        service.download("tiny.en", lambda d, t: None, cancel)


def test_a_restart_waits_for_the_cancelled_download_of_the_same_model(service):
    """review-m3 F2: Cancel then Download again at once. The old download
    may still be inside a read; the new one must not start until it stops."""
    inside, release, events = threading.Event(), threading.Event(), []

    def fake_download(name, folder, progress, verify, manifest, before_write):
        events.append(("start", name))
        if len(events) == 1:  # the first job: a slow read
            inside.set()
            assert release.wait(5), "the test never released the first download"
            before_write()  # it then finds the cancel
        events.append(("end", name))

    service._download = fake_download
    first_cancel, errors = threading.Event(), []

    def run(cancel):
        try:
            service.download("small.en", lambda d, t: None, cancel)
        except Cancelled:
            errors.append("cancelled")

    first = threading.Thread(target=run, args=(first_cancel,))
    first.start()
    assert inside.wait(5)
    first_cancel.set()
    second = threading.Thread(target=run, args=(threading.Event(),))
    second.start()
    second.join(0.3)
    assert events == [("start", "small.en")]  # the restart is waiting
    release.set()
    first.join(5)
    second.join(5)
    assert errors == ["cancelled"]
    assert events == [("start", "small.en"), ("start", "small.en"), ("end", "small.en")]


def test_downloads_of_different_models_do_not_wait_for_each_other(service):
    inside, release, started = threading.Event(), threading.Event(), []

    def fake_download(name, folder, progress, verify, manifest, before_write):
        started.append(name)
        if name == "small.en":
            inside.set()
            assert release.wait(5)

    service._download = fake_download
    first, second = (threading.Thread(target=service.download,
                                      args=(name, lambda d, t: None, threading.Event()))
                     for name in ("small.en", "tiny.en"))
    first.start()
    assert inside.wait(5)
    try:
        second.start()
        second.join(2)
        assert started == ["small.en", "tiny.en"] and not second.is_alive()  # didn't wait
    finally:
        release.set()
        first.join(5)
        second.join(5)


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
    service._download = lambda name, folder, progress, verify, manifest, before_write: (
        verifies.append(verify))
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


def test_cancel_inside_a_read_then_download_again_resumes_cleanly(tmp_path, monkeypatch):
    """review-m3 F2 with the real downloader (the review's race probe): the
    first download is cancelled while blocked in a read, and the same model
    is started again at once. Before the fix both wrote to the .part file
    and the restart failed its checksum, deleting what was fetched."""
    import os

    from test_models import Server, make_manifest

    data = os.urandom(1 << 20)
    manifest = make_manifest({"model.bin": data})
    monkeypatch.setattr(models, "CHUNK", 256 << 10)  # each read waits for 4 pieces
    server = Server({"model.bin": data})
    server.pace = (64 << 10, 0.05)
    try:
        service = ModelService(tmp_path / "models", manifest=manifest, download=(
            lambda *a, **k: models.download(*a, base_url=server.url, timeout=10, **k)))
        results, first_cancel = {}, threading.Event()

        def run(tag, cancel):
            try:
                service.download("fake", lambda d, t: None, cancel)
                results[tag] = "ok"
            except Cancelled:
                results[tag] = "cancelled"
            except Exception as err:  # the bug: ChecksumError
                results[tag] = f"{type(err).__name__}: {err}"

        first = threading.Thread(target=run, args=("first", first_cancel))
        first.start()
        part = models.model_path("fake", service.folder) / "model.bin.part"
        assert wait_for(lambda: part.exists() and part.stat().st_size >= 256 << 10, 10)
        time.sleep(0.1)  # inside the next read
        first_cancel.set()
        second = threading.Thread(target=run, args=("second", threading.Event()))
        second.start()
        first.join(15)
        second.join(30)
        assert results == {"first": "cancelled", "second": "ok"}
        assert (models.model_path("fake", service.folder) / "model.bin").read_bytes() == data
        ranges = [rng for _path, rng in server.requests]
        assert ranges[0] is None and ranges[-1] is not None  # it resumed
    finally:
        server.close()
