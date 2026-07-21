"""Shared helpers for introspecting registered API routes in tests.

FastAPI 0.139 / Starlette 1.3 changed ``include_router`` so that included
routers appear in ``app.routes`` as ``_IncludedRouter`` matcher objects
(exposing ``original_router``) rather than having their child routes copied in.
Collecting endpoint paths therefore requires descending into those matchers.
"""

from __future__ import annotations

from typing import Iterable


def collect_route_paths(routes: Iterable) -> set[str]:
    """Recursively collect every registered path from ``routes``.

    Handles both plain ``Route``/``Mount`` objects (which expose ``.path``)
    and ``_IncludedRouter`` matchers (which expose ``.original_router.routes``).
    """
    paths: set[str] = set()
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path)
        original = getattr(route, "original_router", None)
        if original is not None and hasattr(original, "routes"):
            paths |= collect_route_paths(original.routes)
    return paths
