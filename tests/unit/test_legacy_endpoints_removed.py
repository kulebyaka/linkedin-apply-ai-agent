"""Verify that legacy MVP endpoints have been removed."""

from src.api.main import app


class TestLegacyEndpointsRemoved:
    """Legacy /api/cv/* endpoints should no longer be registered."""

    def _get_routes(self) -> set[str]:
        return {route.path for route in app.routes}

    def test_cv_generate_removed(self):
        assert "/api/cv/generate" not in self._get_routes()

    def test_cv_status_removed(self):
        assert "/api/cv/status/{job_id}" not in self._get_routes()

    def test_cv_download_removed(self):
        assert "/api/cv/download/{job_id}" not in self._get_routes()

    def test_unified_endpoints_still_exist(self):
        routes = self._get_routes()
        assert "/api/jobs/submit" in routes
        assert "/api/jobs/{job_id}/status" in routes
        assert "/api/jobs/{job_id}/pdf" in routes
        assert "/api/hitl/pending" in routes
        assert "/api/hitl/{job_id}/decide" in routes


class TestMvpModuleRemoved:
    """The mvp.py module should no longer exist."""

    def test_mvp_module_not_importable(self):
        try:
            from src.models import mvp  # noqa: F401
            assert False, "src.models.mvp should not be importable"
        except ImportError:
            pass

    def test_job_description_input_in_unified(self):
        from src.models.unified import JobDescriptionInput
        assert JobDescriptionInput is not None
