"""Unit tests for verify_pdf_stack() pre-flight."""

from unittest.mock import patch

import pytest

from src.services.cv import pdf_generator
from src.services.cv.pdf_generator import verify_pdf_stack


def _weasyprint_available() -> bool:
    try:
        from weasyprint.text.ffi import ffi, pango  # noqa: F401
        return True
    except (ImportError, OSError):
        return False


requires_weasyprint_libs = pytest.mark.skipif(
    not _weasyprint_available(),
    reason="WeasyPrint system libraries not available",
)


@requires_weasyprint_libs
def test_verify_pdf_stack_success():
    """On a healthy environment the pre-flight returns (True, None)."""
    ok, hint = verify_pdf_stack()
    assert ok is True
    assert hint is None


def test_verify_pdf_stack_parses_pango_error():
    """A Pango-flavored exception produces a Pango install hint."""

    class _StubHTML:
        def __init__(self, *_args, **_kwargs):
            pass

        def write_pdf(self, *_args, **_kwargs):
            raise OSError("cannot load library 'libpango-1.0.so.0'")

    with patch.object(pdf_generator, "HTML", _StubHTML):
        ok, hint = verify_pdf_stack()

    assert ok is False
    assert hint is not None
    assert "Pango" in hint or "pango" in hint


def test_verify_pdf_stack_parses_cairo_error():
    """A Cairo-flavored exception produces a Cairo install hint."""

    class _StubHTML:
        def __init__(self, *_args, **_kwargs):
            pass

        def write_pdf(self, *_args, **_kwargs):
            raise OSError("libcairo.so.2: cannot open shared object file")

    with patch.object(pdf_generator, "HTML", _StubHTML):
        ok, hint = verify_pdf_stack()

    assert ok is False
    assert hint is not None
    assert "Cairo" in hint or "cairo" in hint


def test_verify_pdf_stack_generic_error():
    """An unrelated exception produces a generic hint that still names the failure."""

    class _StubHTML:
        def __init__(self, *_args, **_kwargs):
            pass

        def write_pdf(self, *_args, **_kwargs):
            raise RuntimeError("totally unexpected boom")

    with patch.object(pdf_generator, "HTML", _StubHTML):
        ok, hint = verify_pdf_stack()

    assert ok is False
    assert hint is not None
    assert "WeasyPrint pre-flight failed" in hint
    assert "boom" in hint
