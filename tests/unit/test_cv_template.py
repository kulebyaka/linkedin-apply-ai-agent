"""Test that ui/src/lib/data/cv_template.json validates against the CV Pydantic model.

The template is shipped to the UI and presented to new users via the
"Load template" button during onboarding. If it ever drifts out of sync
with the CV model, users would be greeted with broken validation
errors on their very first save.
"""

import json
from pathlib import Path

import pytest

from src.models.cv import CV

REPO_ROOT = Path(__file__).resolve().parents[2]
CV_TEMPLATE_PATH = REPO_ROOT / "ui" / "src" / "lib" / "data" / "cv_template.json"


def test_cv_template_file_exists():
    assert CV_TEMPLATE_PATH.exists(), f"Missing: {CV_TEMPLATE_PATH}"


def test_cv_template_parses_as_master_cv():
    raw = json.loads(CV_TEMPLATE_PATH.read_text())
    cv = CV.model_validate(raw)

    assert cv.contact.full_name
    assert cv.contact.email
    assert cv.summary
    assert len(cv.experiences) >= 1
    assert cv.experiences[0].company
    assert cv.experiences[0].position
    assert cv.experiences[0].start_date
    assert len(cv.education) >= 1
    assert len(cv.skills) >= 1


def test_cv_template_round_trips_to_json():
    raw = json.loads(CV_TEMPLATE_PATH.read_text())
    cv = CV.model_validate(raw)
    # Round-trip through Pydantic must not raise.
    cv.model_dump(mode="json")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
