"""Unit tests for the deterministic Easy Apply bridge (ApplyBridge).

A ``MockRelay`` stands in for ``WsRelay``: it records every RPC call and returns
canned reply frames keyed by method, so we exercise classification, daily-limit
detection, submit ordering, PDF encoding, and disconnect→typed-error handling
without any real WebSocket / browser.
"""

from __future__ import annotations

import base64

import pytest

from src.bridge import BridgeDisconnected
from src.config.settings import Settings
from src.models.cv import ContactInfo
from src.models.user import ApplyProfile
from src.services.linkedin.apply_bridge import (
    ApplyBridge,
    ApplyBridgeError,
    ExtensionUnavailable,
)

pytestmark = pytest.mark.asyncio


class MockRelay:
    """Records RPCs and returns canned ``{"type":"result","result":...}`` frames.

    ``responses`` maps method → result dict (or a callable taking ``params``).
    Methods listed in ``disconnect_methods`` raise ``BridgeDisconnected``.
    """

    def __init__(self, responses: dict | None = None) -> None:
        self.responses = responses or {}
        self.disconnect_methods: set[str] = set()
        self.calls: list[tuple[str, dict]] = []
        self.timeouts: list[float] = []

    async def send_rpc(
        self, user_id: str, method: str, params: dict | None = None, timeout: float = 30.0
    ) -> dict:
        self.calls.append((method, params or {}))
        self.timeouts.append(timeout)
        if method in self.disconnect_methods:
            raise BridgeDisconnected(f"no session for {user_id}")
        result = self.responses.get(method, {})
        if callable(result):
            result = result(params or {})
        if isinstance(result, dict) and result.get("__error__"):
            return {"type": "result", "id": "x", "error": result["__error__"]}
        return {"type": "result", "id": "x", "result": result}

    @property
    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


def _settings(**overrides) -> Settings:
    base = {
        "jwt_secret": "x" * 40,
        "apply_rpc_timeout_seconds": 7,
        "apply_daily_limit_detection": True,
    }
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# read_form_state
# ---------------------------------------------------------------------------
class TestReadFormState:
    async def test_classifies_known_and_unknown_fields(self):
        relay = MockRelay(
            {
                "serialize_form": {
                    "step": 1,
                    "total": 3,
                    "fields": [
                        {"selector": "#email", "label": "Email address", "type": "email"},
                        {
                            "selector": "#exp",
                            "label": "Years of experience",
                            "type": "text",
                        },
                        {"selector": "#cv", "label": "Resume", "type": "file"},
                        {
                            "selector": "#brainteaser",
                            "label": "How many golf balls fit in a bus?",
                            "type": "text",
                            "required": True,
                        },
                    ],
                    "flags": {
                        "has_spinner": False,
                        "modal_present": True,
                        "page_text_excerpt": "Contact info",
                    },
                }
            }
        )
        bridge = ApplyBridge(relay, _settings())
        profile = ApplyProfile(years_experience=5)
        contact = ContactInfo(full_name="Ada Lovelace", email="ada@example.com")

        state = await bridge.read_form_state("u1", profile, contact)

        assert state.step == 1 and state.total == 3
        # email + years filled
        kinds = {f.kind for f in state.fill_plan}
        assert kinds == {"email", "years_experience"}
        # file input is skipped (handled by upload_file)
        assert any(s.selector == "#cv" for s in state.skipped)
        # the brain-teaser is unknown → abort signal
        assert len(state.unknown_fields) == 1
        assert state.unknown_fields[0].selector == "#brainteaser"
        assert state.modal_present is True
        assert state.daily_limit_reached is False

    async def test_missing_value_surfaces_as_unknown(self):
        relay = MockRelay(
            {
                "serialize_form": {
                    "fields": [
                        {
                            "selector": "#visa",
                            "label": "Do you require visa sponsorship?",
                            "type": "radio",
                            "options": ["Yes", "No"],
                        }
                    ],
                    "flags": {},
                }
            }
        )
        bridge = ApplyBridge(relay, _settings())
        # profile has no visa answer → recognized-but-missing → Unknown, never a guess
        state = await bridge.read_form_state("u1", ApplyProfile(), None)
        assert not state.fill_plan
        assert len(state.unknown_fields) == 1
        assert "visa" in state.unknown_fields[0].reason

    async def test_daily_limit_detected_from_excerpt(self):
        relay = MockRelay(
            {
                "serialize_form": {
                    "fields": [],
                    "flags": {
                        "page_text_excerpt": "Great effort applying today! "
                        "Continue applying tomorrow."
                    },
                }
            }
        )
        bridge = ApplyBridge(relay, _settings())
        state = await bridge.read_form_state("u1", ApplyProfile(), None)
        assert state.daily_limit_reached is True

    async def test_daily_limit_detection_disabled(self):
        relay = MockRelay(
            {
                "serialize_form": {
                    "fields": [],
                    "flags": {"page_text_excerpt": "Great effort applying today"},
                }
            }
        )
        bridge = ApplyBridge(relay, _settings(apply_daily_limit_detection=False))
        state = await bridge.read_form_state("u1", ApplyProfile(), None)
        assert state.daily_limit_reached is False

    async def test_uses_configured_rpc_timeout(self):
        relay = MockRelay({"serialize_form": {"fields": [], "flags": {}}})
        bridge = ApplyBridge(relay, _settings(apply_rpc_timeout_seconds=12))
        await bridge.read_form_state("u1", ApplyProfile(), None)
        assert relay.timeouts == [12]


# ---------------------------------------------------------------------------
# fill_field
# ---------------------------------------------------------------------------
class TestFillField:
    async def test_fill_field_ok(self):
        relay = MockRelay({"fill_field": {"filled": True}})
        bridge = ApplyBridge(relay, _settings())
        result = await bridge.fill_field("u1", "#email", "ada@example.com")
        assert result == {"filled": True}
        assert relay.calls[0] == (
            "fill_field",
            {"selector": "#email", "value": "ada@example.com"},
        )

    async def test_fill_field_raises_on_error(self):
        relay = MockRelay({"fill_field": {"__error__": "element not found"}})
        bridge = ApplyBridge(relay, _settings())
        with pytest.raises(ApplyBridgeError, match="element not found"):
            await bridge.fill_field("u1", "#missing", "x")


# ---------------------------------------------------------------------------
# advance_step
# ---------------------------------------------------------------------------
class TestAdvanceStep:
    async def test_advances_when_next_clicked_no_errors(self):
        relay = MockRelay(
            {
                "click_button": {"clicked": True},
                "serialize_form": {"fields": [], "flags": {}},
            }
        )
        bridge = ApplyBridge(relay, _settings())
        result = await bridge.advance_step("u1")
        assert result.advanced is True
        assert result.errors == []
        assert relay.calls[0] == ("click_button", {"role": "next"})

    async def test_falls_back_to_review(self):
        def click(params):
            return {"clicked": True} if params.get("role") == "review" else {"clicked": False}

        relay = MockRelay({"click_button": click, "serialize_form": {"fields": [], "flags": {}}})
        bridge = ApplyBridge(relay, _settings())
        result = await bridge.advance_step("u1")
        assert result.advanced is True
        assert [m for m in relay.methods if m == "click_button"] == [
            "click_button",
            "click_button",
        ]

    async def test_surfaces_validation_errors(self):
        relay = MockRelay(
            {
                "click_button": {"clicked": True},
                "serialize_form": {
                    "fields": [],
                    "flags": {"errors": ["This field is required", {"text": "Invalid"}]},
                },
            }
        )
        bridge = ApplyBridge(relay, _settings())
        result = await bridge.advance_step("u1")
        assert result.advanced is False
        assert result.errors == ["This field is required", "Invalid"]


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------
class TestUploadFile:
    async def test_reads_and_encodes_pdf(self, tmp_path):
        pdf = tmp_path / "u1" / "job-42.pdf"
        pdf.parent.mkdir(parents=True)
        payload = b"%PDF-1.7 fake bytes"
        pdf.write_bytes(payload)

        relay = MockRelay({"upload_file": {"uploaded": True}})
        bridge = ApplyBridge(relay, _settings())
        result = await bridge.upload_file("u1", "#cv-input", str(pdf))

        assert result == {"uploaded": True}
        method, params = relay.calls[0]
        assert method == "upload_file"
        assert params["selector"] == "#cv-input"
        assert params["filename"] == "job-42.pdf"
        assert params["mime"] == "application/pdf"
        assert params["dataUrl"].startswith("data:application/pdf;base64,")
        encoded = params["dataUrl"].split(",", 1)[1]
        assert base64.b64decode(encoded) == payload

    async def test_raises_on_upload_error(self, tmp_path):
        pdf = tmp_path / "cv.pdf"
        pdf.write_bytes(b"x")
        relay = MockRelay({"upload_file": {"__error__": "file input not found"}})
        bridge = ApplyBridge(relay, _settings())
        with pytest.raises(ApplyBridgeError, match="file input not found"):
            await bridge.upload_file("u1", "#cv", str(pdf))


# ---------------------------------------------------------------------------
# submit_form
# ---------------------------------------------------------------------------
class TestSubmitForm:
    async def test_unfollow_then_submit_ordering(self):
        relay = MockRelay(
            {
                "unfollow_company": {"unfollowed": True},
                "click_button": {"clicked": True},
                "find_and_click_done": {"clicked": True},
                "capture_visible": {"screenshot_b64": "data:image/png;base64,AAA"},
                "take_screenshot": {"confirmation_text": "Application sent"},
            }
        )
        bridge = ApplyBridge(relay, _settings())
        result = await bridge.submit_form("u1")

        assert result.confirmed is True
        assert result.screenshot_b64 == "data:image/png;base64,AAA"
        assert result.confirmation_text == "Application sent"
        # un-follow must precede the Submit click
        assert relay.methods.index("unfollow_company") < relay.methods.index("click_button")
        assert relay.calls[1] == ("click_button", {"role": "submit"})

    async def test_not_confirmed_when_submit_not_clicked(self):
        relay = MockRelay(
            {
                "unfollow_company": {"unfollowed": False},
                "click_button": {"clicked": False, "disabled": True},
                "find_and_click_done": {"clicked": False},
                "capture_visible": {},
                "take_screenshot": {},
            }
        )
        bridge = ApplyBridge(relay, _settings())
        result = await bridge.submit_form("u1")
        assert result.confirmed is False


# ---------------------------------------------------------------------------
# discard + disconnect handling
# ---------------------------------------------------------------------------
class TestDiscardAndDisconnect:
    async def test_discard_invokes_rpc(self):
        relay = MockRelay({"discard_application": {"discarded": True}})
        bridge = ApplyBridge(relay, _settings())
        result = await bridge.discard("u1", reason="unknown field")
        assert result == {"discarded": True}
        assert relay.methods == ["discard_application"]

    async def test_disconnect_becomes_extension_unavailable(self):
        relay = MockRelay({"serialize_form": {"fields": [], "flags": {}}})
        relay.disconnect_methods.add("serialize_form")
        bridge = ApplyBridge(relay, _settings())
        with pytest.raises(ExtensionUnavailable):
            await bridge.read_form_state("u1", ApplyProfile(), None)

    async def test_disconnect_during_fill(self):
        relay = MockRelay({"fill_field": {"filled": True}})
        relay.disconnect_methods.add("fill_field")
        bridge = ApplyBridge(relay, _settings())
        with pytest.raises(ExtensionUnavailable):
            await bridge.fill_field("u1", "#x", "v")
