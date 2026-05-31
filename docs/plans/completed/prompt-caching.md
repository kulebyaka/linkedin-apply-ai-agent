# Feature Specification: Prompt Caching for LLM Hot Paths

## Overview
- **Feature**: Prompt Caching (OpenAI + OpenAI-API-compatible providers)
- **Status**: Draft
- **Created**: 2026-05-27
- **Author**: User + Claude Code

## Problem Statement

The system makes a high volume of LLM calls with prompts that share large static prefixes. The hottest call path — `JobFilter.evaluate_job` — fires once per scraped LinkedIn job per user/hour, and `CVComposer._compose_all_sections` sends a full master CV (per-user constant) plus a job summary on every CV generation. OpenAI auto-enables prompt caching for prompts ≥1024 tokens and discounts cached input tokens by up to 90%, but **the current prompt templates interpolate variable content (job title, description, summary) near the top**, so almost nothing forms a stable cacheable prefix. Result: we pay full input cost on every call when we could be paying 10% on the bulk of it.

The goal is to restructure prompts and the LLM client API so that all OpenAI-API-compatible providers (OpenAI, Grok, DeepSeek) benefit from automatic prefix caching without further change at the call site.

## Goals & Success Criteria

- Restructure the four targeted prompts so static content forms a long, stable prefix
- Move static content into a `role: "system"` message; variable content into `role: "user"`
- Plumb a `prompt_cache_key` (per-user, per-call-site) through the OpenAI Chat Completions request
- Log `usage.prompt_tokens_details.cached_tokens` per call to verify hit rate from app logs
- **Success Metrics**:
  - After 24h of production traffic: `cached_tokens / prompt_tokens` ≥ 0.5 on `JobFilter.evaluate_job` and `CVComposer._compose_all_sections` (averaged over calls where total prompt tokens ≥1024)
  - No regression in CV/filter output quality (existing unit tests + manual spot-check of 5 generated CVs)
  - Estimated input-token cost on the filter path falls by ≥40% (raw token cost, measured via OpenAI usage dashboard before/after)

## User Stories

1. As the **system operator**, I want hot-path LLM calls to hit the prompt cache so that running cost stays sustainable as user count grows.
2. As a **developer** adding a new LLM call site, I want a typed `PromptSpec` API so the static/variable split is explicit and the cache key is required, not optional.
3. As the **operator debugging cache effectiveness**, I want each LLM call to log `cached_tokens` alongside existing `[TIMING]` lines so I can grep the live log to verify hit rate without opening the OpenAI dashboard.

## Functional Requirements

### Core Capabilities

1. **`PromptSpec` dataclass** replaces the bare `prompt: str` argument across `BaseLLMClient.generate` and `generate_json`. Hard cut — no `str | PromptSpec` union, no parallel methods. All call sites are updated in the same PR.
2. **Two-message request shape** for OpenAI-API-compatible providers: `[{role:"system", content:spec.system}, {role:"user", content:spec.user}]`. If `spec.system` is `None`, fall back to single user message.
3. **`prompt_cache_key` plumbed** into `client.chat.completions.create(..., prompt_cache_key=spec.cache_key)` whenever set.
4. **Prompt template rewrites** for: `prompts/job_filter/default_filter_prompt.txt`, `prompts/cv_composer/full_cv.txt`, `prompts/cv_composer/job_summary.txt`, `prompts/job_filter/generate_prompt_from_prefs.txt`. Each split into a `system.txt` (static) + `user.txt` (variable) pair, or via a clear `---SYSTEM---` / `---USER---` delimiter in a single file (whichever the `PromptLoader` supports more cleanly).
5. **Cached-token logging** added to `_openai_compatible.py:generate` and `generate_json` next to existing `[TIMING]` lines.

### User Flows

#### Flow A — Filter call (JobFilter.evaluate_job)

1. Caller builds `PromptSpec(system=<static filter instructions + scoring rubric + schema>, user=<job_title, company, location, description, user_criteria>, cache_key=f"filter:{user_id}")`.
2. `OpenAICompatibleClient.generate_json(spec, schema=FILTER_SCHEMA, ...)` is invoked.
3. Client sends two-message request with `prompt_cache_key="filter:42"`.
4. Response logged: `[TIMING] OpenAI JSON call completed in 1.21s (cached_tokens=823/1104)`.

#### Flow B — CV compose call (CVComposer._compose_all_sections)

1. Caller builds `PromptSpec(system=<instructions + schema + master_cv JSON>, user=<job_summary + user_feedback_section>, cache_key=f"cv_compose:{user_id}")`.
2. First call for a given user pays full input cost; second+ call for the same user hits the cache for the entire system block (instructions + master CV).
3. When the user updates their master CV via `PUT /api/users/me`, the cache key remains the same but the prefix content changes — OpenAI's cache will simply miss for one call and re-populate. No explicit invalidation needed.

### Data Model

```python
# src/llm/prompt_spec.py (new file)

from dataclasses import dataclass

@dataclass(frozen=True)
class PromptSpec:
    """Cache-aware prompt payload for OpenAI-API-compatible providers.

    - ``system``: static content (instructions, schema, per-user master CV).
      Goes into role="system" — forms the cacheable prefix.
    - ``user``: variable content (job description, job_summary, feedback).
      Goes into role="user" — recomputed each call.
    - ``cache_key``: hint for prompt_cache_key routing. Format
      "<call_site>:<user_id>" (e.g. "filter:42"). Required — pass empty
      string only for one-off calls with no user scope.
    """
    system: str | None
    user: str
    cache_key: str
```

### Integration Points

- `src/llm/base.py` — `BaseLLMClient.generate` / `generate_json` signature changes from `prompt: str` to `spec: PromptSpec`. All concrete providers updated.
- `src/llm/providers/_openai_compatible.py` — assembles two-message payload, passes `prompt_cache_key`, reads `response.usage.prompt_tokens_details.cached_tokens`.
- `src/llm/providers/anthropic.py` — updated to accept `PromptSpec` (uniform interface) but **ignores** `cache_key` and concatenates `spec.system + "\n\n" + spec.user` into a single user message. No Anthropic-side cache_control work in this spec.
- `src/services/cv/cv_prompts.py` — `PromptLoader` gains `load_spec(name, **vars) -> PromptSpec` that reads `{name}.system.txt` and `{name}.user.txt` if present, else falls back to a single-file `---USER---` delimiter parser.
- `src/services/jobs/job_filter.py:79-90` — `_build_evaluation_prompt` returns `PromptSpec`; passes `cache_key=f"filter:{user_id}"`.
- `src/services/cv/cv_composer.py:182, 229` — `_summarize_job` and `_compose_all_sections` construct `PromptSpec` with `cache_key=f"cv_summary:{user_id}"` and `f"cv_compose:{user_id}"` respectively. `user_id` is threaded through from the workflow state (it's already on `JobRecord` and the workflow context).

## Technical Design

### Architecture

Maintains the existing layered LLM architecture (factory → provider client → `BaseLLMClient` ABC). Adds one new module (`src/llm/prompt_spec.py`) and modifies the abstract base + two provider clients. No new external services. The `PromptLoader` gains a single new method; call sites adopt a uniform construction pattern.

### Technology Stack

- **Frameworks**: existing — FastAPI, LangGraph, OpenAI SDK
- **Libraries**: none new
- **Tools**: none new

### Data Persistence

No DB or filesystem state changes. Cache lives entirely on OpenAI's infrastructure (in-memory, 5–10 min default retention). The `prompt_cache_retention="24h"` parameter is **out of scope** for this iteration — revisit only if measured hit rates are insufficient.

### API / Interface Design

```python
# src/llm/base.py — abstract signatures

class BaseLLMClient(ABC):
    @abstractmethod
    def generate(self, spec: PromptSpec, temperature: float = 0.7, **kwargs) -> str: ...

    @abstractmethod
    def generate_json(
        self,
        spec: PromptSpec,
        schema: dict | None = None,
        temperature: float = 0.4,
        max_retries: int = 3,
        **kwargs,
    ) -> dict: ...
```

```python
# src/llm/providers/_openai_compatible.py — request assembly

messages: list[dict] = []
if spec.system:
    messages.append({"role": "system", "content": spec.system})
messages.append({"role": "user", "content": spec.user})

api_kwargs = dict(kwargs)
if spec.cache_key:
    api_kwargs["prompt_cache_key"] = spec.cache_key

response = self.client.chat.completions.create(
    model=self.model,
    messages=messages,
    response_format=response_format,
    **api_kwargs,
)

usage = response.usage
cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0)
logger.info(
    "[TIMING] %s JSON call completed in %.2fs (cached_tokens=%d/%d key=%s)",
    self.provider_label, api_elapsed, cached, usage.prompt_tokens,
    spec.cache_key or "-",
)
```

```text
# prompts/job_filter/default_filter_prompt.system.txt  (static — cacheable)

You are an expert job suitability evaluator. ...
[full instructions + 5 evaluation criteria + scoring rubric + JSON output schema]

$user_criteria_section          # per-user criteria block — still static for that user
```

```text
# prompts/job_filter/default_filter_prompt.user.txt  (variable — NOT cached)

Job Posting:
- Title: $job_title
- Company: $company
- Location: $location
- Description:
$description
```

```text
# prompts/cv_composer/full_cv.system.txt  (static + master_cv — per-user cached)

You are an expert resume writer specializing in ATS-friendly resumes. ...
[full instructions + JSON output schema + quality rules]

Master CV (complete work history):
$master_cv
```

```text
# prompts/cv_composer/full_cv.user.txt  (variable — NOT cached)

Target Job Requirements:
$job_summary

$user_feedback_section
```

## Non-Functional Requirements

- **Performance**: target cache hit rate ≥50% on filter + CV compose paths within 24h of deploy. Per-call latency should not regress (system + user message is the same total payload as before; cached prefix should improve TTFT, not worsen it).
- **Security**: `prompt_cache_key` includes user_id. User_id is a UUID, not PII — safe to send. No CV content or job descriptions appear in the cache key itself.
- **Observability**: every call logs `cached_tokens=<n>/<total>` at INFO level. Grep-friendly format. No new metric backend required.
- **Error Handling**:
  - `usage.prompt_tokens_details` may be absent on older API responses → guard with `getattr` default 0.
  - `prompt_cache_key` is documented as optional; if a provider's API rejects it (e.g. an older OpenAI SDK), the request will fail. Mitigation: pin `openai>=1.55` in `pyproject.toml` if not already.
  - DeepSeek/Grok may ignore `prompt_cache_key` silently — that's fine; auto-caching still applies.

## Implementation Considerations

### Design Trade-offs

| Decision | Considered | Chosen | Rationale |
|---|---|---|---|
| API shape | (a) Hard cut to `PromptSpec`, (b) `str \| PromptSpec` union, (c) parallel `generate_json_cached` | **(a) Hard cut** | Matches CLAUDE.md "no backwards-compat hacks". One PR, one shape, no silent string fallback that would skip caching. |
| CV system content | (a) Instructions + schema only, (b) Instructions + schema + master_cv | **(b) Include master_cv** | Master CV is the biggest chunk (~2–5K tokens) and is per-user constant. Including it in the cached prefix is the whole point — keying by user_id makes this safe. |
| Cache key format | (a) `f"{call_site}:{user_id}"`, (b) `call_site` only, (c) no key | **(a) per-user** | Each user's master CV and custom filter prompt differ; per-user key prevents cross-user cache contention. |
| Providers in scope | (a) OpenAI only, (b) OpenAI + compatible (Grok, DeepSeek), (c) all four including Anthropic | **(b)** | All three share `_openai_compatible.py`, so reorder + system/user split benefits all of them at zero extra cost. Anthropic needs `cache_control` markers — separate work. |
| Rollout | (a) One PR for all hot paths, (b) phased | **(a) One PR** | User explicitly chose this. Blast radius bounded by 4 prompt files + 2 service files + the LLM base/providers. |

### Dependencies

- **OpenAI Python SDK**: `prompt_cache_key` requires `openai>=1.55`. Current state (verified 2026-05-27): `pyproject.toml` pins `openai>=1.10.0` (too loose), `uv.lock` resolves to `openai==2.13.0` (supports it). Action: bump the `pyproject.toml` floor to `openai>=1.55.0` in this PR so a future fresh resolve can't drop below the supporting version.
- The `user_id` must be available at the CV composer call site. Confirmed: `master_cv` is loaded from the User record via `_shared.py` and `user_id` is already on workflow state (it's threaded through `config["configurable"]["user_id"]` in existing nodes — re-check during implementation).

### Testing Strategy

1. **Unit tests**:
   - `tests/llm/test_prompt_spec.py` — construction, frozen-ness.
   - `tests/llm/test_openai_compatible.py` — mock `client.chat.completions.create`, assert two-message payload, `prompt_cache_key` passed, single-message fallback when `system=None`.
   - `tests/services/test_job_filter.py` — assert `PromptSpec.system` contains the rubric and `.user` contains only the job posting.
   - `tests/services/test_cv_composer.py` — assert master_cv lives in `.system`, job_summary in `.user`.
2. **Integration test**: hit a real OpenAI endpoint twice with the same user_id and assert `cached_tokens > 0` on the second call. Mark with `@pytest.mark.integration` so it's opt-in.
3. **Manual verification**: after deploy, tail prod logs for `cached_tokens=` and confirm ≥50% ratio on filter calls within an hour of normal traffic.

## Out of Scope

- **Anthropic prompt caching via `cache_control` markers.** Spec'd as a follow-up if Anthropic becomes a primary provider for filter/compose.
- **`prompt_cache_retention="24h"` extended cache.** Default in-memory retention is acceptable for the per-hour LinkedIn search cadence.
- **PDF extraction call paths (`generate_json_from_pdf`).** Used once during onboarding/master-CV upload — caching has negligible payoff.
- **Prompt cache invalidation on master_cv update.** Cache will miss once and naturally repopulate; no explicit invalidation.
- **Cross-user prompt sharing optimization.** Per-user keys are simpler and avoid edge cases with the per-user `custom_prompt` field.
- **Metric/dashboard integration.** Logs only for v1; revisit if grepping becomes painful.

## Resolved Decisions (formerly open)

- **OpenAI SDK version** — resolved 2026-05-27. Lock currently sits at `openai==2.13.0` (supports `prompt_cache_key`). Bump `pyproject.toml` floor to `openai>=1.55.0` as part of this PR; no other dependency change needed.
- **`generate_prompt_from_preferences` token size** — resolved 2026-05-27. Template is ~400 tokens of instructions + a user free-text block typically 50–500 tokens. Total ≈ 450–900 tokens, **below the 1024 cache threshold**, so OpenAI auto-caching is a no-op there. Still refactor it to `PromptSpec` for API consistency (hard-cut migration) — but do not invest in further optimization. Cache key: `f"filter_prompt_gen:{user_id}"`.
- **`cache_key` in log line** — resolved: include it. Format: `[TIMING] OpenAI JSON call completed in 1.21s (cached_tokens=823/1104 key=filter:42)`. Already reflected in the API / Interface Design section above.

## Open Questions

_None at this time._

## References

- OpenAI prompt caching guide: https://developers.openai.com/api/docs/guides/prompt-caching
- `src/llm/providers/_openai_compatible.py` — central change point
- `src/llm/base.py` — abstract signatures
- `prompts/job_filter/default_filter_prompt.txt` — template to split
- `prompts/cv_composer/full_cv.txt` — template to split
- `src/services/jobs/job_filter.py:79-90` — filter call site
- `src/services/cv/cv_composer.py:188, 239` — CV composer call sites
- CLAUDE.md §"LLM Provider Layer" and §"Common Tasks → Adding a New LLM Provider"
