import hashlib
import http.server
import json
import re
import socket
import threading
import time

import pytest

from transcription_flow import models

FILES = {"config.json": b'{"a": 1}', "model.bin": bytes(range(256)) * 4000,
         "vocabulary.txt": b"hello\nworld\n"}
REPO, REV = "Test/fake-model", "0" * 40


def make_manifest(files=FILES, override_sha=None):
    entries = []
    for name, data in files.items():
        sha = hashlib.sha256(data).hexdigest()
        if override_sha and name in override_sha:
            sha = override_sha[name]
        entries.append({"name": name, "size": len(data), "sha256": sha})
    return {"version": 1, "models": {"fake": {"repo": REPO, "revision": REV, "files": entries}}}


class Server:
    """Local HTTP file server with Range support and fault switches."""

    def __init__(self, files=FILES):
        self.files = dict(files)
        self.requests = []  # (path, range header)
        self.ignore_range = False
        self.missing = set()
        self.cut_after = {}  # name -> bytes sent before the connection drops
        self.delay = 0.0
        self.user_agents = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                server.requests.append((self.path, self.headers.get("Range")))
                server.user_agents.append(self.headers.get("User-Agent"))
                if server.delay:
                    time.sleep(server.delay)
                m = re.fullmatch(rf"/{REPO}/resolve/{REV}/(.+)", self.path)
                name = m.group(1) if m else None
                if name not in server.files or name in server.missing:
                    self.send_error(404)
                    return
                data = server.files[name]
                start = 0
                rng = self.headers.get("Range")
                if rng and not server.ignore_range:
                    start = int(re.fullmatch(r"bytes=(\d+)-", rng).group(1))
                    if start >= len(data):
                        self.send_response(416)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                cut = server.cut_after.pop(name, None)
                if cut is not None:
                    self.wfile.write(body[:cut])
                    self.wfile.flush()
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    srv = Server()
    yield srv
    srv.close()


def fetch(tmp_path, server, manifest=None, **kw):
    return models.download("fake", tmp_path / "models", base_url=server.url,
                           manifest=manifest or make_manifest(), **kw)


def test_download_writes_verified_files_and_reports_progress(tmp_path, server):
    seen = []
    folder = fetch(tmp_path, server, progress=lambda done, total: seen.append((done, total)))
    for name, data in FILES.items():
        assert (folder / name).read_bytes() == data
    assert not list(folder.glob("*.part"))
    total = sum(len(d) for d in FILES.values())
    assert seen[-1] == (total, total)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert models.is_installed("fake", tmp_path / "models", make_manifest())


def test_user_agent_names_the_app_not_a_person(tmp_path, server):
    fetch(tmp_path, server)
    agents = set(server.user_agents)
    assert agents == {models.USER_AGENT}
    assert "github.com/rmcendarfer2017/Transcription-flow" in models.USER_AGENT
    assert "@" not in models.USER_AGENT


def test_resume_sends_range_and_fetches_only_the_rest(tmp_path, server):
    folder = tmp_path / "models" / "fake"
    folder.mkdir(parents=True)
    (folder / "model.bin.part").write_bytes(FILES["model.bin"][:1000])
    seen = []
    fetch(tmp_path, server, progress=lambda d, t: seen.append(d))
    assert (folder / "model.bin").read_bytes() == FILES["model.bin"]
    ranges = {p.rsplit("/", 1)[1]: r for p, r in server.requests}
    assert ranges["model.bin"] == "bytes=1000-"
    assert ranges["config.json"] is None
    assert seen[1] == len(FILES["config.json"]) + 1000  # bytes on disk count as progress


def test_server_ignoring_range_restarts_cleanly(tmp_path, server):
    server.ignore_range = True
    folder = tmp_path / "models" / "fake"
    folder.mkdir(parents=True)
    (folder / "model.bin.part").write_bytes(FILES["model.bin"][:1000])
    seen = []
    fetch(tmp_path, server, progress=lambda d, t: seen.append(d))
    assert (folder / "model.bin").read_bytes() == FILES["model.bin"]
    assert seen[-1] == sum(len(d) for d in FILES.values())


def test_part_file_already_complete_is_verified_without_a_new_request(tmp_path, server):
    folder = tmp_path / "models" / "fake"
    folder.mkdir(parents=True)
    (folder / "model.bin.part").write_bytes(FILES["model.bin"])
    fetch(tmp_path, server)
    assert not any(p.endswith("/model.bin") for p, _ in server.requests)
    assert (folder / "model.bin").read_bytes() == FILES["model.bin"]


def test_oversized_part_file_is_discarded(tmp_path, server):
    folder = tmp_path / "models" / "fake"
    folder.mkdir(parents=True)
    (folder / "model.bin.part").write_bytes(FILES["model.bin"] + b"junk")
    fetch(tmp_path, server)
    assert (folder / "model.bin").read_bytes() == FILES["model.bin"]


def test_checksum_mismatch_is_rejected_and_deleted(tmp_path, server):
    manifest = make_manifest(override_sha={"model.bin": "f" * 64})
    with pytest.raises(models.ChecksumError, match="model.bin"):
        fetch(tmp_path, server, manifest=manifest)
    folder = tmp_path / "models" / "fake"
    assert not (folder / "model.bin").exists() and not (folder / "model.bin.part").exists()
    assert not models.is_installed("fake", tmp_path / "models", manifest)


def test_wrong_size_from_server_is_rejected(tmp_path, server):
    server.files["model.bin"] = FILES["model.bin"] + b"x"
    with pytest.raises(models.ChecksumError):
        fetch(tmp_path, server)


def test_404_gives_a_download_error(tmp_path, server):
    server.missing.add("vocabulary.txt")
    with pytest.raises(models.DownloadError, match="HTTP 404"):
        fetch(tmp_path, server)
    assert not models.is_installed("fake", tmp_path / "models", make_manifest())


def test_timeout_gives_a_download_error(tmp_path, server):
    server.delay = 1.0
    started = time.monotonic()
    with pytest.raises(models.DownloadError):
        fetch(tmp_path, server, timeout=0.2)
    assert time.monotonic() - started < 5


def test_unreachable_server_gives_a_download_error(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing listens here now
    with pytest.raises(models.DownloadError, match="failed"):
        models.download("fake", tmp_path / "models", base_url=f"http://127.0.0.1:{port}",
                        manifest=make_manifest(), timeout=2)


def test_dropped_connection_keeps_the_part_and_a_retry_resumes(tmp_path, server):
    server.cut_after["model.bin"] = 5000
    with pytest.raises(models.DownloadError, match="interrupted"):
        fetch(tmp_path, server)
    part = tmp_path / "models" / "fake" / "model.bin.part"
    assert part.stat().st_size == 5000
    server.requests.clear()
    fetch(tmp_path, server)
    assert ("/" + REPO + "/resolve/" + REV + "/model.bin", "bytes=5000-") in server.requests
    assert models.is_installed("fake", tmp_path / "models", make_manifest())


def test_not_enough_disk_space(tmp_path, server, monkeypatch):
    monkeypatch.setattr(models.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 10})())
    with pytest.raises(models.DownloadError, match="disk space"):
        fetch(tmp_path, server)
    assert server.requests == []


def test_installed_model_makes_no_network_calls(tmp_path, server, monkeypatch):
    fetch(tmp_path, server)

    def no_network(*args, **kwargs):
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    assert models.is_installed("fake", tmp_path / "models", make_manifest())
    folder = models.download("fake", tmp_path / "models", base_url=server.url,
                             manifest=make_manifest())
    assert folder == tmp_path / "models" / "fake"


def test_is_installed_needs_the_marker(tmp_path, server):
    folder = fetch(tmp_path, server)
    (folder / models.MARKER).unlink()
    assert not models.is_installed("fake", tmp_path / "models", make_manifest())


def test_is_installed_rejects_a_changed_file_size(tmp_path, server):
    folder = fetch(tmp_path, server)
    (folder / "vocabulary.txt").write_bytes(b"x")
    assert not models.is_installed("fake", tmp_path / "models", make_manifest())


def test_is_installed_rejects_a_marker_for_another_revision(tmp_path, server):
    folder = fetch(tmp_path, server)
    manifest = make_manifest()
    manifest["models"]["fake"]["revision"] = "1" * 40
    assert not models.is_installed("fake", tmp_path / "models", manifest)
    assert folder.is_dir()


def test_corrupt_complete_file_is_downloaded_again(tmp_path, server):
    folder = fetch(tmp_path, server)
    (folder / models.MARKER).unlink()
    (folder / "config.json").write_bytes(b'{"a": 2}')  # same size, wrong bytes
    server.requests.clear()
    fetch(tmp_path, server)
    assert (folder / "config.json").read_bytes() == FILES["config.json"]
    assert [p.rsplit("/", 1)[1] for p, _ in server.requests] == ["config.json"]


def test_unknown_model_name(tmp_path):
    with pytest.raises(models.ModelError, match="Unknown model"):
        models.download("nope", tmp_path, manifest=make_manifest())


def test_shipped_manifest_is_pinned_and_complete():
    manifest = models.load_manifest()
    assert {"tiny.en", "base.en", "small.en"} <= set(models.model_names(manifest))
    assert models.DEFAULT_MODEL == "small.en"
    for name, spec in manifest["models"].items():
        assert spec["repo"] == f"Systran/faster-whisper-{name}"
        assert re.fullmatch(r"[0-9a-f]{40}", spec["revision"])
        names = {f["name"] for f in spec["files"]}
        assert names == {"config.json", "model.bin", "tokenizer.json", "vocabulary.txt"}
        for f in spec["files"]:
            assert re.fullmatch(r"[0-9a-f]{64}", f["sha256"]) and f["size"] > 0
    small = {f["name"]: f for f in manifest["models"]["small.en"]["files"]}
    assert small["model.bin"]["size"] == 483545366
    json.dumps(manifest)
