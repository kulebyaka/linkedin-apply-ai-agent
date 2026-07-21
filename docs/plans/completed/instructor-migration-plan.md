# Instructor + LiteLLM Migration — Implementation Plan

> Ralphex-executable plan derived from the spec at
> [`docs/plans/instructor-migration.md`](./instructor-migration.md). Read the spec for
> design rationale, spike results (Appendix A), and the Delete/Keep inventory. This plan is
> the task-by-task execution of that design.

## Overview

Replace the four hand-rolled LLM provider adapters and the bespoke `generate_json` loop with
[Instructor](https://python.useinstructor.com/) (structured output via Pydantic) backed by
[LiteLLM](https://github.com/BerriAI/litellm) (provider routing). The public `BaseLLMClient`
interface is preserved so call sites change minimally; all internals are swapped big-bang.
Prompt caching is preserved for both providers; truncation-doubling is dropped in favor of
Instructor's retries.

## Context (from discovery)

- **Files to create:**
  - `src/llm/providers/instructor_client.py` — the single `InstructorClient(BaseLLMClient)`.
- **Files to modify:**
  - `src/llm/base.py` — `BaseLLMClient.generate_json` gains `response_model`; remove
    `LLMTruncatedError`, `build_retry_feedback`, `basic_validate_json_schema`, `DEFAULT_MAX_TOKENS`.
  - `src/llm/provider.py` (re-export shim), `src/llm/providers/__init__.py`.
  - `src/agents/_shared.py::create_llm_client` — build LiteLLM model string, return `InstructorClient`.
  - `src/services/cv/cv_composer.py` — `_summarize_job`, `_compose_all_sections` → `response_model`.
  - `src/services/jobs/job_filter.py` — `evaluate_job`, `generate_refinement` → `response_model`.
  - `src/models/job_filter.py` — add `FilterRefinement` model.
  - `src/models/cv.py` — docstring example (line ~199).
  - `src/api/routes/users.py` — re-home the `supports_pdf` capability check if the factory is removed.
  - `src/services/cv/pdf_extraction.py` — `generate_json_from_pdf` call (signature adds `response_model`).
  - `pyproject.toml` — add `instructor`, `litellm`; remove `jsonschema`.
- **Files to delete** (Tasks 5–6): `src/llm/providers/_openai_compatible.py`, `openai.py`,
  `grok.py`, `deepseek.py`; `src/llm/factory.py`; `src/llm/schema_strict.py`; most of
  `src/llm/providers/anthropic.py`.
- **Keep untouched** (Delete/Keep inventory): `src/llm/prompt_spec.py`, `src/llm/model_catalog.py`,
  `src/llm/pricing_source.py` (**explicit keep decision**), `src/services/jobs/model_catalog_scheduler.py`.
- **Patterns to reuse:** `BaseLLMClient` ABC contract; `PromptSpec` system/user split (feedback
  appended to the user side to preserve the cacheable prefix); `AppContext` DI; the provider→
  LiteLLM-prefix inverse of `pricing_source._PROVIDER_MAP` (`GROK → xai`).
- **Verified facts (spike, litellm 1.93.0 / instructor 1.15.4):** `from_litellm` defaults to
  `Mode.TOOLS`; Anthropic `cache_control` on system content-blocks reaches the wire; OpenAI
  `prompt_cache_key` must go via `extra_body=` (bare kwarg is dropped); `litellm.supports_pdf_input`
  does **not** exist in 1.93.0 (keep `SUPPORTS_PDF_INPUT`).

## Development Approach

- **Testing approach: Regular** (code first, then tests) — matches the existing `src/llm/` test suite.
- Complete each task fully before moving to the next.
- Preserve the `BaseLLMClient` public contract; only add `response_model`.
- Prefer offline tests: mock at the `litellm.completion`/httpx boundary (the spike's
  `httpx.Client.send` intercept is a reusable pattern) so no live keys/spend are needed for unit tests.
- Set `litellm.drop_params = True` and `litellm.telemetry = False` at client-module import.
- **CRITICAL: every task MUST include new/updated tests.**
- **CRITICAL: all tests must pass before starting the next task.**
- **Validation commands** (all via uv):
  - Tests: `uv run pytest`
  - Lint: `uv run ruff check src/ tests/` and `uv run black --check src/ tests/`
  - Types: `uv run mypy src/`

## Implementation Steps

### Task 1: Dependencies + `InstructorClient` (text, JSON, caching)

**Files:**
- Modify: `pyproject.toml`, `src/llm/base.py`
- Create: `src/llm/providers/instructor_client.py`

- [ ] Add `instructor` and `litellm` to `[project].dependencies` in `pyproject.toml`; run `uv lock && uv sync`.
- [ ] In `src/llm/base.py`, add optional `response_model: type[BaseModel] | None = None` param to
      `BaseLLMClient.generate_json` (keep existing `schema`, `validator`, `max_retries`, `temperature`);
      update the return annotation to `dict | BaseModel`. Do NOT yet remove `LLMTruncatedError` etc. (Task 5).
- [ ] Create `src/llm/providers/instructor_client.py` with `InstructorClient(BaseLLMClient)`:
  - [ ] Module-level: `import litellm; litellm.drop_params = True; litellm.telemetry = False`.
  - [ ] `__init__(self, api_key, model, **kwargs)`: store `api_key`/`model`; build
        `self._client = instructor.from_litellm(litellm.completion)`; set `SUPPORTS_PDF_INPUT = True`.
  - [ ] Provider→LiteLLM-prefix map `{OPENAI:"openai", ANTHROPIC:"anthropic", DEEPSEEK:"deepseek", GROK:"xai"}`
        and a `_litellm_model(provider, bare_model) -> f"{prefix}/{bare_model}"` helper (CR-1a). (Provider
        will be threaded in via Task 3; for now accept a pre-built prefixed model string.)
  - [ ] `_build_messages(spec: PromptSpec, *, anthropic: bool)`: system+user; for Anthropic emit the
        system block as a content-block list with `cache_control: {"type": "ephemeral"}`.
  - [ ] `generate(spec, temperature=0.7, **kwargs) -> str`: call `litellm.completion(...)` and return
        `resp.choices[0].message.content`; pass `extra_body={"prompt_cache_key": spec.cache_key}` for
        non-Anthropic when `spec.cache_key`.
  - [ ] `generate_json(spec, response_model=None, schema=None, temperature=0.4, max_retries=3,
        validator=None, **kwargs) -> BaseModel | dict`: require `response_model` (raise if neither given);
        call `self._client.chat.completions.create(model=..., messages=..., response_model=response_model,
        max_retries=max_retries, ...)`; attach `extra_body` cache key for OpenAI-compatible.
- [ ] Write unit tests (`tests/unit/test_instructor_client.py`) using an httpx-send intercept (spike pattern):
  - [ ] Anthropic request carries `cache_control` in the `system` content-block list.
  - [ ] OpenAI request carries `prompt_cache_key` only when passed via `extra_body`.
  - [ ] `generate_json` returns a validated instance of `response_model`.
  - [ ] `generate_json` raises when neither `response_model` nor `schema` is supplied.
- [ ] Run project test suite - must pass before Task 2.

### Task 2: Native PDF input via Instructor multimodal

**Files:**
- Modify: `src/llm/providers/instructor_client.py`, `src/services/cv/pdf_extraction.py`
- Modify (tests): `tests/unit/test_pdf_extraction_api.py`

- [ ] Implement `InstructorClient.generate_json_from_pdf(pdf_bytes, prompt, response_model=None, *,
      temperature=0.1, max_tokens=8192) -> BaseModel | dict` using Instructor multimodal / document
      input (base64 PDF block) with `response_model` validation. Support both OpenAI and Anthropic
      PDF-capable models via the same LiteLLM path.
- [ ] Keep `SUPPORTS_PDF_INPUT = True` and the `NotImplementedError` contract for non-PDF models.
- [ ] Update `src/services/cv/pdf_extraction.py::run_extraction` to pass a `response_model`
      (the CV extraction Pydantic model) instead of the raw `_CV_JSON_SCHEMA` dict; keep the
      "retry once with reminder" fallback semantics.
- [ ] Update `tests/unit/test_pdf_extraction_api.py` to exercise the Instructor PDF path (mock the
      LiteLLM boundary; assert a validated model is returned and NotImplementedError still surfaces
      for unsupported models).
- [ ] Run project test suite - must pass before Task 3.

### Task 3: Wire `create_llm_client` to `InstructorClient`; retire the factory

**Files:**
- Modify: `src/agents/_shared.py`, `src/llm/factory.py` (retire), `src/api/routes/users.py`
- Modify (tests): factory/`_shared` tests, `tests/unit/test_llm_models_api.py` if it touches the factory

- [ ] In `src/agents/_shared.py::create_llm_client`, keep the settings resolution (provider + per-provider
      `*_model`/`*_api_key`), then build the LiteLLM-prefixed model string via the provider→prefix map
      (CR-1a, `GROK → xai`) and return `InstructorClient(api_key, litellm_model_str)`.
- [ ] Retire `LLMClientFactory`: either delete `src/llm/factory.py` and update imports, or reduce it to
      return `InstructorClient` for every provider. Re-home the `supports_pdf` capability check used by
      `src/api/routes/users.py` (e.g. a module-level `provider_supports_pdf(provider) -> bool` or a
      constant), since `litellm.supports_pdf_input` is unavailable in 1.93.0.
- [ ] Update `src/api/routes/users.py` to use the re-homed capability check.
- [ ] Update/replace the factory + `_shared` init tests to assert the correct prefixed model string per
      provider (esp. `grok` → `xai/grok-4`) and that an `InstructorClient` is returned.
- [ ] Run project test suite - must pass before Task 4.

### Task 4: Migrate call sites to `response_model`

**Files:**
- Modify: `src/services/cv/cv_composer.py`, `src/services/jobs/job_filter.py`,
  `src/models/job_filter.py`, `src/models/cv.py`
- Modify (tests): `tests/unit/test_job_filter_models.py`, CV composer tests, `tests/unit/test_llm_generate_json.py`

- [ ] Add a `FilterRefinement` Pydantic model to `src/models/job_filter.py`
      (`proposed_learned_block: str`, `rationale: str`) to replace `JobFilter._REFINEMENT_SCHEMA`.
- [ ] `src/services/jobs/job_filter.py`:
  - [ ] `evaluate_job`: call `self.llm.generate_json(spec, response_model=FilterResult,
        temperature=self.TEMPERATURE)`; the result is already a validated `FilterResult` — delete the
        trailing `FilterResult(**raw_result)` re-validation and the `validator=` lambda.
  - [ ] `generate_refinement`: use `response_model=FilterRefinement`; keep the "malformed block" guard.
  - [ ] `generate_prompt_from_preferences`: unchanged (still uses `self.llm.generate(spec, ...)`).
- [ ] `src/services/cv/cv_composer.py`:
  - [ ] `_summarize_job`: `response_model=JobSummary`; return `.model_dump()`; drop the duplicate
        `JobSummary(**summary)` re-validation and the `validator=` lambda.
  - [ ] `_compose_all_sections`: `response_model=CVLLMOutput`; return the dict/model as the downstream
        length-limit code expects (adjust `_apply_length_limits` input if it now receives a model).
- [ ] Update `src/models/cv.py` docstring example (~line 199) to the `response_model` form.
- [ ] Update the affected unit tests to the new call shape (mock the client's `generate_json` to return
      model instances); assert `FilterRefinement` validation and CV/job-summary composition.
- [ ] Run project test suite - must pass before Task 5.

### Task 5: Delete redundant provider/loop code; drop `jsonschema`

**Files:**
- Delete: `src/llm/providers/_openai_compatible.py`, `providers/openai.py`, `providers/grok.py`,
  `providers/deepseek.py`; `src/llm/factory.py` (if not already in Task 3)
- Modify: `src/llm/providers/anthropic.py` (delete file if fully replaced), `src/llm/base.py`,
  `src/llm/provider.py`, `src/llm/providers/__init__.py`, `pyproject.toml`
- Delete (tests): obsolete `tests/unit/test_anthropic_client.py`, DeepSeek-path parts of
  `tests/unit/test_llm_generate_json.py`

- [ ] Delete the four provider modules (D1–D3) and `factory.py` (D4); update `providers/__init__.py`
      to export only `InstructorClient`.
- [ ] Remove from `src/llm/base.py` (D5–D7): `build_retry_feedback`, `basic_validate_json_schema`,
      `LLMTruncatedError`, `DEFAULT_MAX_TOKENS`. Keep `BaseLLMClient` + `LLMProvider`.
- [ ] Update `src/llm/provider.py` re-export shim to drop removed symbols; fix any importers.
- [ ] Remove `jsonschema` from `pyproject.toml` dependencies (D6 — sole consumer was
      `basic_validate_json_schema`); run `uv lock && uv sync`.
- [ ] Delete/rewrite obsolete tests; grep for dangling imports of deleted symbols
      (`rg 'make_schema_strict|LLMTruncatedError|basic_validate_json_schema|OpenAICompatibleClient'`).
- [ ] Run project test suite - must pass before Task 6.

### Task 6: Live gate → delete `schema_strict.py`

**Files:**
- Delete: `src/llm/schema_strict.py`
- Modify: `src/llm/provider.py` (drop re-exports), any importers
- Delete (tests): schema-strict unit tests

- [ ] **GATE (requires a real `ANTHROPIC_API_KEY`):** run one live `JobFilter.evaluate_job` against
      Anthropic with the real `FilterResult` schema (carries `minimum`/`maximum`) and confirm: (a) **no
      400** on schema shape, and (b) `cache_read_input_tokens` is **non-zero** on an immediate identical
      second call. Record the result in the plan. If it 400s on schema shape, STOP: restore
      `make_schema_anthropic_safe` as a thin pre-flight transform and skip the deletion below.
  - **GATE RESULT (2026-07-21): GREEN.** With a valid `ANTHROPIC_API_KEY`, a live
    `JobFilter.evaluate_job` against `anthropic/claude-sonnet-4-5` returned a validated
    `FilterResult` (score=85) with **no 400** on the tool `input_schema` (which carries
    `minimum`/`maximum`). Caching confirmed: a large stable system prefix produced
    `cache_creation_input_tokens=5850` on call 1 and `cache_read_input_tokens=5850` on an immediate
    identical repeat. (A live OpenAI smoke was also green.)
- [x] Only if the gate is green: delete `src/llm/schema_strict.py` (D10 — no consumer under `Mode.TOOLS`);
      remove `make_schema_strict`/`make_schema_anthropic_safe` from `provider.py` exports and any imports.
- [x] Remove/rewrite schema-strict tests. **(N/A — the only schema-strict test, `test_anthropic_client.py`,
      was already removed in Task 5; no dedicated `schema_strict` test remains.)**
- [x] Run project test suite - must pass before Task 7.

### Task 7: Verify acceptance criteria

- [ ] Manual test: submit one job end-to-end (filter → CV compose → PDF) on the configured provider and
      confirm a valid `FilterResult` and CV PDF are produced.
- [ ] Caching parity: on a repeated filter/compose call for the same user, confirm cached tokens fire
      (Anthropic `cache_read_input_tokens` / OpenAI `cached_tokens` non-zero in the `[TIMING]` logs).
- [ ] Provider smoke (where keys available): one `evaluate_job` per configured provider (OpenAI +
      Anthropic at minimum; Grok/DeepSeek if keys present — Open Question #5 on tool-calling support).
- [ ] Run full test suite: `uv run pytest`
- [ ] Run linters: `uv run ruff check src/ tests/` and `uv run black --check src/ tests/`
- [ ] Run types: `uv run mypy src/`
- [ ] Verify test coverage ≥80% for the new `instructor_client.py`.

### Task 8: Update documentation

- [ ] Update `CLAUDE.md` LLM sections: strict-schema/provider notes, `generate_json` resilience, and the
      "Adding a New LLM Provider" steps to reflect Instructor + LiteLLM + `Mode.TOOLS`.
- [ ] Note the retained `pricing_source.py` / model-catalog behavior is unchanged.
- [ ] Update `README.md` only if user-facing behavior changed (it should not).
- [ ] Move both `docs/plans/instructor-migration.md` (spec) and this plan to `docs/plans/completed/`.

## Notes / Risks (see spec for detail)

- **Anthropic schema-shape 400** — de-risked by `Mode.TOOLS` (spike), but Task 6's live gate is the
  authoritative check before deleting `schema_strict.py`.
- **Dropped truncation-doubling** — large CV compositions could burn retries on `max_tokens`; set a
  sensible default `max_tokens` in `_compose_all_sections` and watch logs (Open Question #4).
- **`pricing_source.py` stays** — explicit decision; do not swap to in-process `litellm.model_cost`.
- **Grok/DeepSeek** — unverified in the spike; confirm tool-calling works, else use Instructor `Mode.JSON`
  for those (Open Question #5).
