"""Benchmark prompt-cache effectiveness for the filter + CV-compose paths.

Runs three layered checks (each catches a different failure mode):

  1. Token-threshold check — does spec.system cross OpenAI's 1024-token
     cache floor? If not, caching never engages.
  2. Prefix-stability check — does the same (call_site, user_id) produce
     byte-identical spec.system across builds? If not, caching can't hit
     even at 10k tokens.
  3. Live hit-rate check — hits the real OpenAI API twice in quick
     succession with the same cache_key and reads cached_tokens from
     usage. Call 1 seeds; call 2 should report most of the system block
     as cached.

Usage:
    OPENAI_API_KEY=sk-... uv run python -m scripts.benchmark_prompt_cache
    uv run python -m scripts.benchmark_prompt_cache --skip-live   # offline only
    uv run python -m scripts.benchmark_prompt_cache --model gpt-4o-mini

Exit codes:
    0 — all checks passed
    1 — a check failed; details printed to stderr
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from src.llm.prompt_spec import PromptSpec
from src.services.cv.cv_prompts import CVPromptManager
from src.services.jobs.job_filter import JobFilter

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "unit" / "fixtures"


def _count_tokens(text: str, model: str) -> int:
    """Token count via tiktoken if available, else a 4-char-per-token estimate."""
    try:
        import tiktoken
    except ImportError:
        return len(text) // 4
    try:
        enc = tiktoken.encoding_for_model(model)
    except KeyError:
        enc = tiktoken.get_encoding("cl100k_base")
    return len(enc.encode(text))


def _load_fixtures() -> tuple[dict, dict]:
    master_cv = json.loads((FIXTURES / "master_cv.json").read_text())
    if "contact" not in master_cv:
        master_cv["contact"] = master_cv.get(
            "contact_info",
            {"full_name": "Test User", "email": "test@example.com"},
        )
    job_posting = json.loads((FIXTURES / "job_posting.json").read_text())
    return master_cv, job_posting


def _build_filter_spec(job_posting: dict, user_id: str) -> PromptSpec:
    # JobFilter only needs the LLM client for actual evaluation; building
    # the spec doesn't touch it.
    jf = JobFilter(llm_client=None)  # type: ignore[arg-type]
    return jf._build_evaluation_spec(job_posting, None, user_id)


def _build_cv_compose_spec(master_cv: dict, user_id: str) -> PromptSpec:
    return CVPromptManager().get_full_cv_spec(
        master_cv=master_cv,
        job_summary={
            "technical_skills": ["Python"],
            "soft_skills": ["Communication"],
            "education_reqs": [],
            "experience_reqs": {"years": 5, "level": "senior"},
            "responsibilities": ["Build things"],
            "nice_to_have": [],
        },
        cache_key=f"cv_compose:{user_id}",
    )


def check_token_threshold(specs: dict[str, PromptSpec], model: str) -> bool:
    print("\n[1] Token-threshold check (need spec.system ≥1024 tokens)")
    ok = True
    for name, spec in specs.items():
        system_tokens = _count_tokens(spec.system or "", model)
        user_tokens = _count_tokens(spec.user, model)
        status = "OK  " if system_tokens >= 1024 else "FAIL"
        if system_tokens < 1024:
            ok = False
        print(
            f"  [{status}] {name:20s} system={system_tokens:5d} tok  "
            f"user={user_tokens:5d} tok"
        )
    return ok


def check_prefix_stability(builders: dict[str, callable]) -> bool:
    print("\n[2] Prefix-stability check (same inputs → byte-identical system)")
    ok = True
    for name, build in builders.items():
        a = build()
        b = build()
        if a.system != b.system:
            ok = False
            print(f"  [FAIL] {name}: system blocks differ across builds")
            # Find first diverging char for the debug hint.
            for i, (ca, cb) in enumerate(zip(a.system or "", b.system or "")):
                if ca != cb:
                    ctx = (a.system or "")[max(0, i - 20) : i + 20]
                    print(f"         first diff at char {i}: …{ctx!r}…")
                    break
        else:
            print(f"  [OK  ] {name}: identical across two builds")
    return ok


def check_live_cache(specs: dict[str, PromptSpec], model: str) -> bool:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("\n[3] Live cache check — SKIPPED (set OPENAI_API_KEY to run)")
        return True

    print(f"\n[3] Live cache check against OpenAI (model={model})")
    try:
        from openai import OpenAI
    except ImportError:
        print("  [SKIP] openai package not installed", file=sys.stderr)
        return True

    client = OpenAI(api_key=api_key)
    ok = True
    for name, spec in specs.items():
        messages: list[dict] = []
        if spec.system:
            messages.append({"role": "system", "content": spec.system})
        messages.append({"role": "user", "content": spec.user})

        cached_per_call: list[int] = []
        prompt_per_call: list[int] = []
        for attempt in range(2):
            t0 = time.time()
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=64,
                prompt_cache_key=spec.cache_key,
            )
            elapsed = time.time() - t0
            usage = resp.usage
            details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(details, "cached_tokens", 0) if details else 0
            cached_per_call.append(cached)
            prompt_per_call.append(usage.prompt_tokens)
            print(
                f"  {name} call {attempt + 1}: "
                f"prompt={usage.prompt_tokens} cached={cached} "
                f"({elapsed:.2f}s)"
            )

        # Verdict: second call should cache at least half the prompt tokens.
        if cached_per_call[1] < prompt_per_call[1] * 0.5:
            ok = False
            print(
                f"  [FAIL] {name}: second call cached only "
                f"{cached_per_call[1]}/{prompt_per_call[1]} — expected ≥50%"
            )
        else:
            ratio = cached_per_call[1] / prompt_per_call[1]
            print(f"  [OK  ] {name}: second-call hit rate {ratio:.0%}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="gpt-4o",
        help="OpenAI model id (default: gpt-4o; cache needs gpt-4o or newer)",
    )
    parser.add_argument(
        "--user-id",
        default="bench-user-1",
        help="user_id used in cache_key (default: bench-user-1)",
    )
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Skip the live OpenAI API check (offline-only run)",
    )
    args = parser.parse_args()

    master_cv, job_posting = _load_fixtures()

    builders = {
        "filter": lambda: _build_filter_spec(job_posting, args.user_id),
        "cv_compose": lambda: _build_cv_compose_spec(master_cv, args.user_id),
    }
    specs = {name: build() for name, build in builders.items()}

    t_ok = check_token_threshold(specs, args.model)
    s_ok = check_prefix_stability(builders)
    if args.skip_live:
        print("\n[3] Live cache check — SKIPPED (--skip-live)")
        l_ok = True
    else:
        l_ok = check_live_cache(specs, args.model)

    print("\n" + "=" * 60)
    print(f"  Token threshold:  {'PASS' if t_ok else 'FAIL'}")
    print(f"  Prefix stability: {'PASS' if s_ok else 'FAIL'}")
    print(f"  Live cache hits:  {'PASS' if l_ok else 'FAIL'}")
    print("=" * 60)

    return 0 if (t_ok and s_ok and l_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
