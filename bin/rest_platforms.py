"""
bin/rest_platforms.py - restmap.conf [script:dsv_platforms]
(/datasource_validator/platforms, capability.get = dsv_view_results,
capability.post = dsv_manage_datasources).

List platforms (with roll-up status) and create a new one. See
app/rest_validation_shared.py for the shared helpers every platform/
data-source/run/queue/results handler uses, and its docstring for why
these handlers are split one-per-file.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app.rest_validation_shared import (  # noqa: E402
    default_stale_threshold,
    platform_dict,
    stores,
)
from app.status_calculator import compute_datasource_display_status  # noqa: E402


class DsvPlatformsHandler(rest_base.DsvPersistentHandler):
    def handle_get(self, request):
        st = stores(request.session_key)
        threshold = default_stale_threshold(request.session_key)
        platforms = st.platform_store.list()
        datasources = st.datasource_store.list()

        by_platform = defaultdict(list)
        for ds in datasources:
            by_platform[ds.platform_id].append(
                compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
            )

        return {
            "platforms": [platform_dict(p, by_platform.get(p.platform_id, [])) for p in platforms]
        }

    def handle_post(self, request):
        payload = request.payload or {}
        st = stores(request.session_key)
        platform = st.platform_store.create(
            platform_id=payload.get("platform_id"),
            name=payload.get("name"),
            description=payload.get("description", ""),
            sort_order=payload.get("sort_order", 0),
            enabled=payload.get("enabled", True),
        )
        return platform_dict(platform, []), 201
