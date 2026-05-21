"""In-memory PDF CV extraction task registry and worker.

The registry stores per-process extraction tasks. Exactly one task per
user is retained — a new upload evicts the previous record for the same
user. Lost on restart by design — the spec accepts re-upload as the
recovery path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import ValidationError

from src.llm.provider import BaseLLMClient
from src.models.cv import CV
from src.services.cv.cv_prompts import CV_EXTRACTION_PROMPT

_CV_JSON_SCHEMA = CV.model_json_schema()
_IN_FLIGHT_STATUSES = ("pending", "running")

logger = logging.getLogger(__name__)

ExtractionStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class CVExtractionTask:
    """An in-memory record of a single PDF CV extraction attempt."""

    id: str
    user_id: str
    status: ExtractionStatus = "pending"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    result_json: dict | None = None
    validation_errors: list[str] = field(default_factory=list)
    error_message: str | None = None


class CVExtractionRegistry:
    """Per-process in-memory store for CV extraction tasks.

    One task per user is retained — creating a new task for a user
    evicts the previous one, keeping memory bounded by the number of
    distinct users.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, CVExtractionTask] = {}
        self._by_user: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create(self, user_id: str) -> CVExtractionTask:
        async with self._lock:
            return self._create_locked(user_id)

    def _create_locked(self, user_id: str) -> CVExtractionTask:
        prev_id = self._by_user.get(user_id)
        if prev_id is not None:
            self._by_id.pop(prev_id, None)
        task = CVExtractionTask(id=str(uuid.uuid4()), user_id=user_id)
        self._by_id[task.id] = task
        self._by_user[user_id] = task.id
        return task

    async def create_if_not_in_flight(
        self, user_id: str
    ) -> CVExtractionTask | None:
        """Atomically create a new task unless the user already has one
        in a non-terminal state.

        Returns the new task, or None if an in-flight task already exists.
        """
        async with self._lock:
            prev_id = self._by_user.get(user_id)
            if prev_id is not None:
                prev = self._by_id.get(prev_id)
                if prev is not None and prev.status in _IN_FLIGHT_STATUSES:
                    return None
            return self._create_locked(user_id)

    async def get(self, extraction_id: str) -> CVExtractionTask | None:
        async with self._lock:
            return self._by_id.get(extraction_id)

    async def get_latest_for_user(self, user_id: str) -> CVExtractionTask | None:
        async with self._lock:
            tid = self._by_user.get(user_id)
            if tid is None:
                return None
            return self._by_id.get(tid)

    async def update(self, extraction_id: str, **fields: object) -> None:
        async with self._lock:
            task = self._by_id.get(extraction_id)
            if task is None:
                raise KeyError(extraction_id)
            for key, value in fields.items():
                setattr(task, key, value)


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """Render Pydantic validation errors as user-friendly strings."""
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        msg = err.get("msg", "invalid")
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


async def run_extraction(
    task: CVExtractionTask,
    pdf_bytes: bytes,
    llm_client: BaseLLMClient,
    registry: CVExtractionRegistry,
) -> None:
    """Background worker: extract CV JSON from PDF, validate, update task.

    Never raises — failures are recorded on the task itself. Pydantic
    validation failures are not task failures: the task completes with
    ``validation_errors`` populated so the UI can surface them alongside
    the raw JSON.
    """
    start = time.monotonic()
    logger.info(
        "PDF extraction started: user=%s task=%s size=%d bytes model=%s",
        task.user_id, task.id, len(pdf_bytes), llm_client.model,
    )

    await registry.update(task.id, status="running")

    try:
        try:
            raw = await asyncio.to_thread(
                llm_client.generate_json_from_pdf,
                pdf_bytes,
                CV_EXTRACTION_PROMPT,
                _CV_JSON_SCHEMA,
            )
        except json.JSONDecodeError:
            logger.warning("PDF extraction returned malformed JSON, retrying once")
            raw = await asyncio.to_thread(
                llm_client.generate_json_from_pdf,
                pdf_bytes,
                CV_EXTRACTION_PROMPT
                + "\n\nReminder: return ONLY a valid JSON object. No commentary, no markdown fences.",
                _CV_JSON_SCHEMA,
            )
    except NotImplementedError as e:
        # Should have been caught at the API layer; defensive fallback.
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.error("PDF extraction not supported: %s (%.0fms)", e, elapsed_ms)
        await registry.update(
            task.id,
            status="failed",
            error_message="The configured CV model does not support PDF input.",
        )
        return
    except Exception as e:  # noqa: BLE001
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.exception(
            "PDF extraction failed: user=%s task=%s (%.0fms)",
            task.user_id, task.id, elapsed_ms,
        )
        await registry.update(
            task.id,
            status="failed",
            error_message=f"LLM extraction failed: {e}",
        )
        return

    if not isinstance(raw, dict):
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.error(
            "PDF extraction returned non-object JSON: user=%s task=%s type=%s (%.0fms)",
            task.user_id, task.id, type(raw).__name__, elapsed_ms,
        )
        await registry.update(
            task.id,
            status="failed",
            error_message="LLM returned a non-object JSON value; cannot map to a CV.",
        )
        return

    validation_errors: list[str] = []
    try:
        CV.model_validate(raw)
    except ValidationError as ve:
        validation_errors = _format_validation_errors(ve)
        logger.warning(
            "PDF extraction produced invalid CV JSON: user=%s task=%s errors=%d",
            task.user_id, task.id, len(validation_errors),
        )

    elapsed_ms = (time.monotonic() - start) * 1000
    logger.info(
        "PDF extraction completed: user=%s task=%s duration_ms=%.0f validation_errors=%d",
        task.user_id, task.id, elapsed_ms, len(validation_errors),
    )
    await registry.update(
        task.id,
        status="completed",
        result_json=raw,
        validation_errors=validation_errors,
    )
