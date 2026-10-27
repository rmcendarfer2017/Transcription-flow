"""Static checks of the user docs (M5): links resolve, and the privacy page
names what the code really does (model sources, telemetry switches)."""

import functools
import re
from pathlib import Path

import pytest

from transcription_flow import REPO_URL, _env, logsetup, output, paths
from transcription_flow.models import load_manifest, size_label

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "PRIVACY.md", ROOT / "CHANGELOG.md",
        *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\]\(([^)\s]+)\)")


@functools.cache
def anchors(path):
    """GitHub's heading anchors: lower case, punctuation dropped, spaces to -."""
    out = set()
    for heading in re.findall(r"^#+ (.+)$", path.read_text("utf-8"), re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        out.add(slug)
    return out


def test_the_docs_exist():
    names = {p.relative_to(ROOT).as_posix() for p in DOCS}
    assert {"README.md", "PRIVACY.md", "CHANGELOG.md", "docs/smartscreen.md",
            "docs/code-signing-policy.md"} <= names


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_links_point_at_files_and_headings_that_exist(doc):
    for target in LINK.findall(doc.read_text("utf-8")):
        if re.match(r"[a-z]+:", target):
            continue
        file, _, anchor = target.partition("#")
        path = (doc.parent / file).resolve() if file else doc
        assert path.exists(), f"{doc.name}: {target}"
        if anchor:
            assert anchor in anchors(path), f"{doc.name}: {target}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_the_app_is_inspired_by_wispr_flow_never_a_clone(doc):
    text = doc.read_text("utf-8")
    assert not re.search(r"\bclon(e|es|ed|ing)\b", text, re.I), doc.name


def test_the_readme_links_the_release_and_the_smartscreen_steps():
    readme = (ROOT / "README.md").read_text("utf-8")
    assert "inspired by Wispr Flow" in readme
    assert f"]({REPO_URL}/releases/latest)" in readme
    assert "**More info**, then\n   **Run anyway**" in readme
    for page in ("PRIVACY.md", "docs/smartscreen.md"):
        assert f"]({page}" in readme, page


def test_the_privacy_page_names_every_model_source_the_app_downloads_from():
    privacy = (ROOT / "PRIVACY.md").read_text("utf-8")
    for name, spec in load_manifest()["models"].items():
        assert f"`{spec['repo']}`" in privacy, name
    assert "https://huggingface.co/<repo>/resolve/<revision>/<file>" in privacy


def test_the_privacy_page_lists_the_telemetry_switches_the_app_sets():
    privacy = (ROOT / "PRIVACY.md").read_text("utf-8")
    for var in ("ORT_DISABLE_TELEMETRY", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_OFFLINE"):
        assert _env.PRIVACY_ENV[var] == "1"
        assert f"{var}=1" in privacy, var


def windows_path(path):
    return str(path).replace("/", "\\")


def test_the_privacy_page_numbers_and_paths_are_the_codes(monkeypatch):
    """review-m5 F9: rotation, paste wait and folders come from the code."""
    privacy = (ROOT / "PRIVACY.md").read_text("utf-8")
    assert (f"it rotates\nat {logsetup.MAX_BYTES / 1e6:g} MB and keeps {logsetup.BACKUPS} old files"
            in privacy)
    assert f"({output.PASTE_SETTLE_SECONDS:g} s by default)" in privacy
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.delenv("TFLOW_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", "%LOCALAPPDATA%")
    monkeypatch.setenv("APPDATA", "%APPDATA%")
    for shown in (f"`{windows_path(paths.logs_dir() / logsetup.LOG_NAME)}`",
                  f"`{windows_path(paths.models_dir())}\\`",
                  f"`{windows_path(paths.data_dir())}`",
                  f"`{windows_path(paths.config_file())}`"):
        assert shown in privacy, shown


def model_sizes():
    return {name: sum(f["size"] for f in spec["files"])
            for name, spec in load_manifest()["models"].items()}


def test_model_sizes_in_the_docs_are_the_ones_the_app_shows():
    """QA B8: one number per model, size_label of the manifest (decimal MB)."""
    sizes = model_sizes()
    privacy = (ROOT / "PRIVACY.md").read_text("utf-8")
    readme = (ROOT / "README.md").read_text("utf-8")
    for name, size in sizes.items():
        assert re.search(rf"{re.escape(name)}(\s\(the\s+recommended one\))?\s+about\s+"
                         rf"{re.escape(size_label(size))}", privacy), name
    assert f"`small.en` (about {size_label(sizes['small.en'])})" in readme
    explorer = f"{sizes['small.en'] // 2**20} MB"  # Explorer: MiB, cut to 3 digits
    assert f"small.en as about {explorer}" in privacy
    allowed = {size_label(s) for s in sizes.values()} | {explorer}
    for doc, text in (("PRIVACY.md", privacy), ("README.md", readme)):
        for number in re.findall(r"about\s+(\d+ MB)", text):
            assert number in allowed, (doc, number)


def rendered(path):
    """The text GitHub shows: HTML comments dropped."""
    return re.sub(r"<!--.*?-->", "", path.read_text("utf-8"), flags=re.S)


def test_the_signing_policy_marks_unmet_rules_as_todo():
    """review-m5 F1: nothing that is not in force yet reads as a fact."""
    policy = rendered(ROOT / "docs" / "code-signing-policy.md")
    items = re.split(r"\n(?:- |\| )", policy)
    for rule in ("multi-factor", "ruleset for `main`", "tag ruleset for `v*`"):
        (item,) = [i for i in items if rule in i]
        assert item.startswith("**TODO (maintainer):**"), rule
    assert "through a pull request with passing CI" not in policy
    assert "Changes to `main` go through pull requests" not in policy
    assert "released unsigned" not in policy and "will ship unsigned" in policy
    assert "guard against mistakes, not against someone who" in policy
