"""Tests for the Chrome MV3 extension (Task 3).

Two layers:

* ``test_manifest_*`` — pure-Python validation that ``manifest.json`` is valid
  JSON with the required MV3 keys/permissions and deliberately has NO
  declarative ``content_scripts`` block (inject-on-demand security model).
* ``test_content_script_primitives`` — drives the JSDOM harness
  (``extension/tests/content_script.test.mjs``) via ``node --test`` so the DOM
  primitives (``serialize_form`` / ``fill_field`` / ``unfollow_company``) run
  against the captured Easy Apply modal fixture. Skipped when ``node`` or the
  ``jsdom`` dependency is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = REPO_ROOT / "extension"
MANIFEST_PATH = EXTENSION_DIR / "manifest.json"
NODE_TEST = EXTENSION_DIR / "tests" / "content_script.test.mjs"
UI_NODE_MODULES = REPO_ROOT / "ui" / "node_modules" / "jsdom"


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_is_valid_mv3(manifest: dict) -> None:
    assert manifest["manifest_version"] == 3
    assert manifest["name"]
    assert manifest["version"]
    # Background must be an MV3 service worker (not a persistent page).
    assert manifest["background"]["service_worker"] == "background.js"


def test_manifest_has_required_permissions(manifest: dict) -> None:
    for perm in ("tabs", "storage", "scripting"):
        assert perm in manifest["permissions"], f"missing permission: {perm}"
    assert any(
        h.startswith("https://www.linkedin.com") for h in manifest["host_permissions"]
    )


def test_manifest_has_no_declarative_content_scripts(manifest: dict) -> None:
    # Security model: the content script is injected ON DEMAND via
    # chrome.scripting, never auto-loaded on every LinkedIn page.
    assert "content_scripts" not in manifest


def test_manifest_externally_connectable_lists_app_origin(manifest: dict) -> None:
    matches = manifest["externally_connectable"]["matches"]
    assert any("localhost:5173" in m for m in matches), matches


def test_referenced_files_exist(manifest: dict) -> None:
    for rel in (
        manifest["background"]["service_worker"],
        manifest["action"]["default_popup"],
        "content_script.js",
        "popup/popup.js",
        "popup/popup.css",
    ):
        assert (EXTENSION_DIR / rel).is_file(), f"missing file: {rel}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(
    not UI_NODE_MODULES.is_dir(),
    reason="jsdom not installed (run `npm install` in ui/)",
)
def test_content_script_primitives() -> None:
    """Run the JSDOM unit tests for serialize_form() / fill_field()."""
    result = subprocess.run(
        ["node", "--test", str(NODE_TEST)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"node --test failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
