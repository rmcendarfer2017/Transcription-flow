"""Static checks of the user docs (M5): links resolve, and the privacy page
names what the code really does (model sources, telemetry switches)."""

import json
import re
from pathlib import Path

import pytest

from transcription_flow import _env

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", ROOT / "PRIVACY.md", ROOT / "CHANGELOG.md",
        *sorted((ROOT / "docs").glob("*.md"))]
LINK = re.compile(r"\]\(([^)\s]+)\)")


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
    assert "](https://github.com/rmcendarfer2017/Transcription-flow/releases/latest)" in readme
    assert "**More info**, then\n   **Run anyway**" in readme
    for page in ("PRIVACY.md", "docs/smartscreen.md"):
        assert f"]({page}" in readme, page


def test_the_privacy_page_names_every_model_source_the_app_downloads_from():
    privacy = (ROOT / "PRIVACY.md").read_text("utf-8")
    manifest = json.loads((ROOT / "src" / "transcription_flow" / "models_manifest.json")
                          .read_text("utf-8"))
    for name, spec in manifest["models"].items():
        assert f"`{spec['repo']}`" in privacy, name
    assert "https://huggingface.co/<repo>/resolve/<revision>/<file>" in privacy


def test_the_privacy_page_lists_the_telemetry_switches_the_app_sets():
    privacy = (ROOT / "PRIVACY.md").read_text("utf-8")
    for var in ("ORT_DISABLE_TELEMETRY", "HF_HUB_DISABLE_TELEMETRY", "HF_HUB_OFFLINE"):
        assert _env.PRIVACY_ENV[var] == "1"
        assert f"{var}=1" in privacy, var
