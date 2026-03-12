"""E2E test fixtures for HITL review UI tests.

Provides session-scoped fixtures that auto-start:
1. FastAPI backend (subprocess with LLM mocked via _test_api_server.py)
2. Vite UI dev server (subprocess)
3. Playwright browser

Both servers are started as subprocesses and killed on teardown.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent.parent
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_server(url: str, timeout: float = 30) -> None:
    """Poll *url* until it responds with HTTP 2xx or *timeout* expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=3)
            if r.status_code < 400:
                return
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
            pass
        time.sleep(0.5)
    raise TimeoutError(f"Server at {url} did not become ready within {timeout}s")


def _subprocess_env(**extra) -> dict:
    """Build an environment dict inheriting key vars from the parent process."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "PYTHONPATH": str(PROJECT_ROOT),
        # WeasyPrint needs gobject from Homebrew on macOS
        "DYLD_LIBRARY_PATH": os.environ.get(
            "DYLD_LIBRARY_PATH",
            "/opt/homebrew/lib",
        ),
    }
    # Forward any .env-style vars the API needs (repo_type defaults to memory)
    for key in ("REPO_TYPE", "VIRTUAL_ENV"):
        val = os.environ.get(key)
        if val:
            env[key] = val
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# FastAPI server fixture (subprocess with mocked LLM)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mock_llm_and_api_server():
    """Start FastAPI with LLM calls mocked; yield the base URL."""
    port = _free_port()
    api_url = f"http://127.0.0.1:{port}"

    server_script = str(Path(__file__).parent / "_test_api_server.py")
    proc = subprocess.Popen(
        [sys.executable, server_script, str(port)],
        cwd=str(PROJECT_ROOT),
        env=_subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        wait_for_server(f"{api_url}/api/health", timeout=30)
    except TimeoutError:
        proc.kill()
        stdout = proc.stdout.read().decode() if proc.stdout else ""
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        raise TimeoutError(
            f"API server failed to start on {api_url}.\n"
            f"stdout: {stdout[:2000]}\nstderr: {stderr[:2000]}"
        )

    yield api_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Vite UI dev server fixture (subprocess)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def ui_dev_server(mock_llm_and_api_server):
    """Start Vite dev server; yield the UI base URL.

    The VITE_API_BASE_URL env var points the UI to the mock API server.
    """
    api_url = mock_llm_and_api_server
    port = _free_port()
    ui_url = f"http://localhost:{port}"

    ui_dir = PROJECT_ROOT / "ui"

    proc = subprocess.Popen(
        ["npx", "vite", "dev", "--port", str(port), "--strictPort"],
        cwd=str(ui_dir),
        env=_subprocess_env(
            NODE_ENV="development",
            VITE_API_BASE_URL=api_url,
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        wait_for_server(ui_url, timeout=60)
    except TimeoutError:
        proc.kill()
        stdout = proc.stdout.read().decode() if proc.stdout else ""
        stderr = proc.stderr.read().decode() if proc.stderr else ""
        raise TimeoutError(
            f"Vite dev server failed to start on {ui_url}.\n"
            f"stdout: {stdout[:2000]}\nstderr: {stderr[:2000]}"
        )

    yield ui_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Playwright fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def browser():
    """Launch Playwright Chromium for the test session."""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """Create a new browser page per test, close after."""
    pg = browser.new_page()
    yield pg
    pg.close()
