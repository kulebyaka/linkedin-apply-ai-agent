"""E2E test fixtures for HITL review UI tests.

Provides session-scoped fixtures that auto-start:
1. FastAPI backend (subprocess with LLM mocked via _test_api_server.py)
2. Vite UI dev server (subprocess)
3. Playwright browser

Both servers are started as subprocesses and killed on teardown.
"""

import json
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_server(f"{api_url}/api/health", timeout=30)
    except TimeoutError:
        proc.kill()
        raise TimeoutError(f"API server failed to start on {api_url}")

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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        wait_for_server(ui_url, timeout=60)
    except TimeoutError:
        proc.kill()
        raise TimeoutError(f"Vite dev server failed to start on {ui_url}")

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


# ---------------------------------------------------------------------------
# Job seeding fixtures
# ---------------------------------------------------------------------------

SAMPLE_JOB_POSTING = {
    "title": "Senior Python Backend Engineer",
    "company": "AI Startup",
    "description": (
        "We're looking for an experienced Python backend engineer to join our "
        "growing team. You'll be responsible for designing and implementing "
        "scalable microservices architecture for our AI-powered platform. The "
        "ideal candidate has strong experience with Python, Django, AWS, and "
        "containerization technologies."
    ),
    "requirements": (
        "5+ years of Python development experience, Strong knowledge of Django "
        "or Flask, Experience with AWS services (EC2, S3, Lambda, RDS), Docker "
        "and Kubernetes expertise, PostgreSQL or similar relational database "
        "experience, Experience with RESTful API design, Strong problem-solving "
        "skills, Excellent communication abilities"
    ),
}


def _submit_and_wait_pending(api_url: str, job_data: dict, timeout: float = 60) -> str:
    """Submit a job in full mode and poll until it reaches 'pending' status.

    Returns the job_id.
    """
    payload = {
        "source": "manual",
        "mode": "full",
        "job_description": job_data,
    }
    resp = httpx.post(f"{api_url}/api/jobs/submit", json=payload, timeout=10)
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status_resp = httpx.get(f"{api_url}/api/jobs/{job_id}/status", timeout=10)
        status_resp.raise_for_status()
        status = status_resp.json()["status"]
        if status == "pending":
            return job_id
        if status == "failed":
            raise RuntimeError(
                f"Job {job_id} failed: {status_resp.json().get('error_message')}"
            )
        time.sleep(1)

    raise TimeoutError(f"Job {job_id} did not reach 'pending' within {timeout}s (last: {status})")


@pytest.fixture
def seed_pending_job(mock_llm_and_api_server):
    """Submit one job via API and wait until it reaches 'pending' status.

    Returns the job_id.
    """
    return _submit_and_wait_pending(mock_llm_and_api_server, SAMPLE_JOB_POSTING)


@pytest.fixture
def seed_two_pending_jobs(mock_llm_and_api_server):
    """Submit two jobs and wait for both to reach 'pending' status.

    Returns a list of two job_ids.
    """
    job1 = _submit_and_wait_pending(mock_llm_and_api_server, SAMPLE_JOB_POSTING)
    second_job = {
        **SAMPLE_JOB_POSTING,
        "title": "ML Platform Engineer",
        "company": "DataCorp",
    }
    job2 = _submit_and_wait_pending(mock_llm_and_api_server, second_job)
    return [job1, job2]


# Build a long job description (500+ words) with a unique marker near the end.
_LONG_DESCRIPTION_PARAGRAPHS = [
    (
        "We are seeking an experienced Senior Python Backend Engineer to join our "
        "rapidly growing engineering team. In this role, you will design and build "
        "scalable, high-performance backend services that power our AI-driven "
        "platform. You will collaborate closely with data scientists, frontend "
        "engineers, and product managers to deliver impactful features."
    ),
    (
        "Our technology stack is built on Python, FastAPI, and PostgreSQL, with "
        "services deployed on AWS using Docker and Kubernetes. We practice "
        "continuous integration and deployment, code reviews, and automated "
        "testing. We believe in pragmatic engineering decisions and keeping our "
        "codebase clean and maintainable."
    ),
    (
        "As a senior engineer, you will mentor junior team members, contribute "
        "to architectural discussions, and help shape our engineering culture. "
        "You will be responsible for designing APIs, optimizing database queries, "
        "implementing caching strategies, and ensuring our services meet strict "
        "SLA requirements. You will also participate in on-call rotations."
    ),
    (
        "We value engineers who are passionate about building great software, "
        "who take ownership of their work, and who are always looking for ways "
        "to improve. Our team is distributed across multiple time zones, so "
        "strong written communication skills are essential. We hold weekly "
        "architecture reviews and monthly tech talks."
    ),
    (
        "The ideal candidate has deep experience with Python web frameworks, "
        "message queues like RabbitMQ or Kafka, and monitoring tools such as "
        "Prometheus and Grafana. Experience with machine learning pipelines, "
        "data processing at scale, and event-driven architectures is a plus. "
        "We offer competitive compensation, equity, and flexible work arrangements."
    ),
    (
        "Additional responsibilities include writing technical documentation, "
        "conducting performance benchmarks, managing database migrations, and "
        "integrating third-party services. You will work with our DevOps team "
        "to improve CI/CD pipelines and infrastructure as code practices."
    ),
    (
        "Our company is at the forefront of applying AI to solve real-world "
        "problems in the enterprise space. We have raised Series B funding and "
        "are scaling rapidly. This is an opportunity to make a significant "
        "impact at a company that values engineering excellence."
    ),
    (
        "UNIQUE_END_MARKER_PARAGRAPH — This final section confirms the full "
        "job description is visible without truncation. If you can read this "
        "paragraph, the description has not been clipped by a read-more button "
        "or CSS overflow rules."
    ),
]

LONG_DESCRIPTION_JOB_POSTING = {
    "title": "Senior Python Backend Engineer",
    "company": "AI Startup",
    "description": "\n\n".join(_LONG_DESCRIPTION_PARAGRAPHS),
    "requirements": SAMPLE_JOB_POSTING["requirements"],
}


@pytest.fixture
def seed_long_description_job(mock_llm_and_api_server):
    """Submit a job with a 500+ word description and wait for 'pending' status.

    Returns the job_id.
    """
    return _submit_and_wait_pending(mock_llm_and_api_server, LONG_DESCRIPTION_JOB_POSTING)
