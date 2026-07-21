"""Unit + integration tests for the auto-refining filter prompt feature.

Covers:
- Learned-block marker helpers (apply/extract: present, absent, malformed).
- JobFilter.generate_refinement with a mock LLM (deterministic + malformed).
- UserRepository pending-proposal CRUD + get_all_with_auto_refine.
- NotificationRepository CRUD, mark-read, mark-read-by-type, user scoping.
- Repository refine-signal queries (list/mark).
- run_refinement_cycle: min-signal gate + end-to-end proposal + notification.
- Signal capture on decline (with reason) and proceed-anyway (with reason).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.llm.prompt_spec import PromptSpec
from src.llm.provider import BaseLLMClient
from src.models.job_filter import (
    AUTO_LEARNED_BEGIN,
    AUTO_LEARNED_END,
    UserFilterPreferences,
    apply_learned_block,
    extract_learned_block,
)
from src.models.state_machine import BusinessState
from src.models.unified import JobRecord
from src.services.db.job_repository import InMemoryJobRepository

# ---------------------------------------------------------------------------
# Mock LLM client
# ---------------------------------------------------------------------------


class _MockLLM(BaseLLMClient):
    def __init__(self, json_response: dict | None = None):
        super().__init__(api_key="test", model="test-model")
        self._json = json_response

    def generate(self, spec: PromptSpec, temperature: float = 0.7, **kwargs) -> str:
        return "unused"

    def generate_json(
        self, spec, response_model=None, schema=None, temperature=0.4, max_retries=3, **kwargs
    ):
        data = self._json or {}
        if response_model is not None:
            return response_model(**data)
        return data


# ---------------------------------------------------------------------------
# Learned-block helpers
# ---------------------------------------------------------------------------


def test_apply_learned_block_appends_when_absent():
    out = apply_learned_block("My hand-written rules.", "## Auto-learned criteria\n- x")
    assert "My hand-written rules." in out
    assert out.count(AUTO_LEARNED_BEGIN) == 1
    assert out.count(AUTO_LEARNED_END) == 1
    assert extract_learned_block(out) == "## Auto-learned criteria\n- x"


def test_apply_learned_block_replaces_only_region():
    base = apply_learned_block("HAND", "## Auto-learned criteria\n- old")
    out = apply_learned_block(base, "## Auto-learned criteria\n- new")
    assert "HAND" in out
    assert "new" in out
    assert "old" not in out
    assert out.count(AUTO_LEARNED_BEGIN) == 1


def test_apply_learned_block_none_base():
    out = apply_learned_block(None, "## Auto-learned criteria\n- x")
    assert out.startswith(AUTO_LEARNED_BEGIN)


def test_extract_learned_block_absent_returns_none():
    assert extract_learned_block(None) is None
    assert extract_learned_block("just user text") is None


def test_extract_learned_block_malformed_returns_none():
    # END before BEGIN
    bad = f"{AUTO_LEARNED_END}\nstuff\n{AUTO_LEARNED_BEGIN}"
    assert extract_learned_block(bad) is None


# ---------------------------------------------------------------------------
# JobFilter.generate_refinement
# ---------------------------------------------------------------------------


def test_generate_refinement_returns_block_and_rationale():
    from src.services.jobs.job_filter import JobFilter

    llm = _MockLLM(
        {
            "proposed_learned_block": "## Auto-learned criteria\nDEALBREAKERS (auto-disqualify):\n- Requires on-site presence",
            "rationale": "You declined several on-site roles.",
        }
    )
    jf = JobFilter(llm)
    result = jf.generate_refinement("", ["Role A: on-site"], [], user_id="u1")
    assert "## Auto-learned criteria" in result["proposed_learned_block"]
    assert result["rationale"]


def test_generate_refinement_rejects_malformed_block():
    from src.services.jobs.job_filter import JobFilter, JobFilterError

    llm = _MockLLM({"proposed_learned_block": "no heading here", "rationale": "x"})
    jf = JobFilter(llm)
    with pytest.raises(JobFilterError):
        jf.generate_refinement("", ["a", "b"], [], user_id="u1")


# ---------------------------------------------------------------------------
# Repository refine-signal queries
# ---------------------------------------------------------------------------


def _job(job_id: str, user_id: str = "u1", **kw) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        user_id=user_id,
        source="linkedin",
        mode="full",
        status=BusinessState.PENDING,
        **kw,
    )


@pytest.mark.asyncio
async def test_inmemory_refine_signal_roundtrip():
    repo = InMemoryJobRepository()
    await repo.initialize()
    await repo.create(_job("j1"))
    await repo.update(
        "j1",
        {
            "status": BusinessState.DECLINED,
            "decline_reason": "too junior",
            "refine_signal_state": "pending",
        },
    )
    pending = await repo.list_refine_signals("u1", "pending")
    assert [j.job_id for j in pending] == ["j1"]
    # scoping
    assert await repo.list_refine_signals("other", "pending") == []
    await repo.mark_refine_signals(["j1"], "consumed")
    assert await repo.list_refine_signals("u1", "pending") == []
    assert len(await repo.list_refine_signals("u1", "consumed")) == 1


# ---------------------------------------------------------------------------
# UserRepository pending-proposal + opt-in listing
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def user_repo(tmp_path):
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.db.migrations import apply_migrations
    from src.services.db.tables import MagicLinkTable, NotificationTable, UserTable

    db_path = tmp_path / "test_refine_users.db"
    engine = SQLiteEngine(path=str(db_path))
    UserTable._meta._db = engine
    MagicLinkTable._meta._db = engine
    NotificationTable._meta._db = engine
    await UserTable.create_table(if_not_exists=True).run()
    await MagicLinkTable.create_table(if_not_exists=True).run()
    await NotificationTable.create_table(if_not_exists=True).run()
    await apply_migrations(engine)

    from src.services.auth.user_repository import UserRepository

    yield UserRepository()
    await engine.close_connection_pool()


@pytest.mark.asyncio
async def test_pending_proposal_set_get_clear(user_repo):
    from src.models.job_filter import RefinementProposal

    user = await user_repo.create_user("refine@example.com")
    assert await user_repo.get_pending_proposal(user.id) is None

    proposal = RefinementProposal(
        proposed_learned_block="## Auto-learned criteria\n- x",
        rationale="because",
        signal_job_ids=["j1", "j2"],
        decline_count=2,
        override_count=0,
    )
    await user_repo.set_pending_proposal(user.id, proposal)
    loaded = await user_repo.get_pending_proposal(user.id)
    assert loaded is not None
    assert loaded.signal_job_ids == ["j1", "j2"]
    assert loaded.decline_count == 2

    await user_repo.clear_pending_proposal(user.id)
    assert await user_repo.get_pending_proposal(user.id) is None


@pytest.mark.asyncio
async def test_get_all_with_auto_refine(user_repo):
    opted_in = await user_repo.create_user("yes@example.com")
    await user_repo.update(
        opted_in.id,
        {"filter_preferences": UserFilterPreferences(auto_refine_enabled=True)},
    )
    opted_out = await user_repo.create_user("no@example.com")
    await user_repo.update(
        opted_out.id,
        {"filter_preferences": UserFilterPreferences(auto_refine_enabled=False)},
    )
    await user_repo.create_user("none@example.com")  # no filter prefs

    users = await user_repo.get_all_with_auto_refine()
    assert {u.email for u in users} == {"yes@example.com"}


# ---------------------------------------------------------------------------
# NotificationRepository
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def notif_repo(tmp_path):
    from piccolo.engine.sqlite import SQLiteEngine

    from src.services.db.tables import NotificationTable

    db_path = tmp_path / "test_notifs.db"
    engine = SQLiteEngine(path=str(db_path))
    NotificationTable._meta._db = engine
    await NotificationTable.create_table(if_not_exists=True).run()

    from src.services.notifications.notification_repository import NotificationRepository

    yield NotificationRepository()
    await engine.close_connection_pool()


@pytest.mark.asyncio
async def test_notification_crud_and_scoping(notif_repo):
    await notif_repo.create(
        "u1", type="filter_refinement", title="A", action_url="/settings#filter"
    )
    await notif_repo.create("u1", type="other", title="B")
    await notif_repo.create("u2", type="filter_refinement", title="C")

    assert await notif_repo.unread_count("u1") == 2
    assert await notif_repo.unread_count("u2") == 1

    u1 = await notif_repo.list_for_user("u1")
    assert len(u1) == 2
    # cross-user isolation
    assert all(n.user_id == "u1" for n in u1)

    # mark one read
    ok = await notif_repo.mark_read(u1[0].id, "u1")
    assert ok
    assert await notif_repo.unread_count("u1") == 1
    # cannot mark another user's notification
    other = await notif_repo.list_for_user("u2")
    assert await notif_repo.mark_read(other[0].id, "u1") is False

    # mark all read
    await notif_repo.mark_all_read("u1")
    assert await notif_repo.unread_count("u1") == 0


@pytest.mark.asyncio
async def test_notification_mark_read_by_type(notif_repo):
    await notif_repo.create("u1", type="filter_refinement", title="A")
    await notif_repo.create("u1", type="filter_refinement", title="B")
    await notif_repo.create("u1", type="other", title="C")
    updated = await notif_repo.mark_read_by_type("u1", "filter_refinement")
    assert updated == 2
    assert await notif_repo.unread_count("u1") == 1  # the 'other' one


# ---------------------------------------------------------------------------
# run_refinement_cycle (gate + end-to-end)
# ---------------------------------------------------------------------------


class _Ctx:
    """Minimal AppContext stand-in for run_refinement_cycle."""

    def __init__(self, repository, user_repository, notification_repository, settings):
        self.repository = repository
        self.user_repository = user_repository
        self.notification_repository = notification_repository
        self.settings = settings


# ---------------------------------------------------------------------------
# Signal capture: decline-with-reason and proceed-with-reason
# ---------------------------------------------------------------------------


class _DeclineCtx:
    def __init__(self, repository):
        self.repository = repository


@pytest.mark.asyncio
async def test_decline_with_reason_captures_signal():
    from src.models.unified import HITLDecision
    from src.services.jobs.hitl_processor import HITLProcessor

    repo = InMemoryJobRepository()
    await repo.initialize()
    await repo.create(_job("d1"))

    processor = HITLProcessor(_DeclineCtx(repo))
    await processor.process_decision(
        "d1", HITLDecision(decision="declined", reasoning="Too junior for me"), "u1"
    )
    rec = await repo.get("d1")
    assert rec.status == BusinessState.DECLINED
    assert rec.decline_reason == "Too junior for me"
    assert rec.refine_signal_state == "pending"


@pytest.mark.asyncio
async def test_decline_without_reason_no_signal():
    from src.models.unified import HITLDecision
    from src.services.jobs.hitl_processor import HITLProcessor

    repo = InMemoryJobRepository()
    await repo.initialize()
    await repo.create(_job("d2"))

    processor = HITLProcessor(_DeclineCtx(repo))
    await processor.process_decision("d2", HITLDecision(decision="declined"), "u1")
    rec = await repo.get("d2")
    assert rec.status == BusinessState.DECLINED
    assert rec.decline_reason is None
    assert rec.refine_signal_state is None


@pytest.mark.asyncio
async def test_refinement_gate_skips_below_min(user_repo, notif_repo, monkeypatch):
    from src.config.settings import get_settings
    from src.services.jobs import refinement

    repo = InMemoryJobRepository()
    await repo.initialize()
    user = await user_repo.create_user("gate@example.com")
    await user_repo.update(
        user.id, {"filter_preferences": UserFilterPreferences(auto_refine_enabled=True)}
    )
    user = await user_repo.get_by_id(user.id)

    # 2 signals, min is 10 by default → skip.
    for i in range(2):
        await repo.create(_job(f"g{i}", user_id=user.id))
        await repo.update(
            f"g{i}",
            {
                "status": BusinessState.DECLINED,
                "decline_reason": "nope",
                "refine_signal_state": "pending",
            },
        )

    ctx = _Ctx(repo, user_repo, notif_repo, get_settings())
    result = await refinement.run_refinement_cycle(ctx, user)
    assert result is None
    assert await notif_repo.unread_count(user.id) == 0
    # signals remain pending
    assert len(await repo.list_refine_signals(user.id, "pending")) == 2


@pytest.mark.asyncio
async def test_refinement_creates_proposal_and_notification(user_repo, notif_repo, monkeypatch):
    from src.config.settings import get_settings
    from src.services.jobs import refinement

    repo = InMemoryJobRepository()
    await repo.initialize()
    user = await user_repo.create_user("full@example.com")
    await user_repo.update(
        user.id, {"filter_preferences": UserFilterPreferences(auto_refine_enabled=True)}
    )
    user = await user_repo.get_by_id(user.id)

    for i in range(10):
        await repo.create(
            _job(f"s{i}", user_id=user.id, job_posting={"title": f"Role {i}", "company": "Acme"})
        )
        await repo.update(
            f"s{i}",
            {
                "status": BusinessState.DECLINED,
                "decline_reason": "too junior",
                "refine_signal_state": "pending",
            },
        )

    # Stub the LLM client factory via a fake module so we don't import the real
    # src.agents._shared (which pulls in WeasyPrint native libs).
    import sys
    import types

    def _fake_create_llm_client(provider=None, model=None):
        return _MockLLM(
            {
                "proposed_learned_block": "## Auto-learned criteria\nDEALBREAKERS (auto-disqualify):\n- Requires < 5 years experience",
                "rationale": "You keep declining junior roles.",
            }
        )

    fake_shared = types.ModuleType("src.agents._shared")
    fake_shared.create_llm_client = _fake_create_llm_client
    monkeypatch.setitem(sys.modules, "src.agents._shared", fake_shared)

    ctx = _Ctx(repo, user_repo, notif_repo, get_settings())
    proposal = await refinement.run_refinement_cycle(ctx, user)

    assert proposal is not None
    assert proposal.decline_count == 10
    assert "## Auto-learned criteria" in proposal.proposed_learned_block
    # stored on user
    stored = await user_repo.get_pending_proposal(user.id)
    assert stored is not None
    # signals moved to proposed
    assert await repo.list_refine_signals(user.id, "pending") == []
    assert len(await repo.list_refine_signals(user.id, "proposed")) == 10
    # notification emitted
    assert await notif_repo.unread_count(user.id) == 1
    notifs = await notif_repo.list_for_user(user.id)
    assert notifs[0].type == "filter_refinement"
    assert notifs[0].action_url == "/settings#filter"


@pytest.mark.asyncio
async def test_decline_on_override_job_does_not_capture_signal():
    """A forced-through ("Proceed Anyway") job declined later is not a filter
    false-positive: no decline signal is captured and the override signal is
    left intact rather than reset to 'pending'."""
    from src.models.unified import HITLDecision
    from src.services.jobs.hitl_processor import HITLProcessor

    repo = InMemoryJobRepository()
    await repo.initialize()
    await repo.create(_job("o1", override_reason="genuinely remote", refine_signal_state="pending"))

    processor = HITLProcessor(_DeclineCtx(repo))
    await processor.process_decision(
        "o1", HITLDecision(decision="declined", reasoning="changed my mind"), "u1"
    )
    rec = await repo.get("o1")
    assert rec.status == BusinessState.DECLINED
    assert rec.decline_reason is None
    assert rec.override_reason == "genuinely remote"
    assert rec.refine_signal_state == "pending"  # untouched, not resurrected


@pytest.mark.asyncio
async def test_refinement_skips_when_proposal_pending(user_repo, notif_repo):
    """An un-acknowledged proposal blocks a new cycle, so signals are neither
    re-fed nor superseded and no duplicate notification is emitted."""
    from src.config.settings import get_settings
    from src.models.job_filter import RefinementProposal
    from src.services.jobs import refinement

    repo = InMemoryJobRepository()
    await repo.initialize()
    user = await user_repo.create_user("pending@example.com")
    await user_repo.update(
        user.id, {"filter_preferences": UserFilterPreferences(auto_refine_enabled=True)}
    )
    user = await user_repo.get_by_id(user.id)

    await user_repo.set_pending_proposal(
        user.id,
        RefinementProposal(
            proposed_learned_block="## Auto-learned criteria\n- x",
            rationale="already pending",
            signal_job_ids=[],
        ),
    )

    # Even with enough fresh signals, the cycle must skip.
    for i in range(10):
        await repo.create(_job(f"p{i}", user_id=user.id))
        await repo.update(
            f"p{i}",
            {
                "status": BusinessState.DECLINED,
                "decline_reason": "nope",
                "refine_signal_state": "pending",
            },
        )

    ctx = _Ctx(repo, user_repo, notif_repo, get_settings())
    result = await refinement.run_refinement_cycle(ctx, user)

    assert result is None
    assert len(await repo.list_refine_signals(user.id, "pending")) == 10
    assert await notif_repo.unread_count(user.id) == 0


def test_apply_learned_block_strips_nested_markers():
    """A proposed block that itself contains markers must not produce a nested
    marker pair (which would corrupt later extract/replace)."""
    poisoned = f"{AUTO_LEARNED_BEGIN}\n## Auto-learned criteria\n- x\n{AUTO_LEARNED_END}"
    out = apply_learned_block("HAND", poisoned)
    assert out.count(AUTO_LEARNED_BEGIN) == 1
    assert out.count(AUTO_LEARNED_END) == 1
    assert "HAND" in out
    assert extract_learned_block(out) == "## Auto-learned criteria\n- x"
