"""
bin/rest_datasources.py - restmap.conf [script:dsv_datasources]
(/datasource_validator/datasources, capability.get = dsv_view_results,
capability.post = dsv_manage_datasources).

List data sources (optionally filtered by platform) and create a new
one. See app/rest_validation_shared.py for the shared helpers every
platform/data-source/run/queue/results handler uses, and its docstring
for why these handlers are split one-per-file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app import security  # noqa: E402
from app.models.base import NotFoundError  # noqa: E402
from app.rest_validation_shared import (  # noqa: E402
    datasource_dict,
    default_stale_threshold,
    is_admin,
    stores,
    validate_query_or_raise,
)


class DsvDatasourcesHandler(rest_base.DsvPersistentHandler):
    def handle_get(self, request):
        st = stores(request.session_key)
        threshold = default_stale_threshold(request.session_key)
        platform_id = request.query.get("platform_id")
        datasources = st.datasource_store.list(platform_id=platform_id)
        return {"datasources": [datasource_dict(ds, threshold) for ds in datasources]}

    def handle_post(self, request):
        payload = request.payload or {}
        spl_query = payload.get("spl_query")
        if not spl_query:
            raise ValueError("spl_query is required")
        validate_query_or_raise(request.session_key, spl_query, is_admin(request.session_key))

        st = stores(request.session_key)
        platform_id = payload.get("platform_id")
        security.validate_identifier(platform_id or "", "platform_id")
        if st.platform_store.get(platform_id) is None:
            raise NotFoundError(f"no such platform: {platform_id}")

        ds = st.datasource_store.create(
            datasource_id=payload.get("datasource_id"),
            platform_id=platform_id,
            name=payload.get("name"),
            spl_query=spl_query,
            description=payload.get("description", ""),
            sort_order=payload.get("sort_order", 0),
            enabled=payload.get("enabled", True),
            stale_threshold_sec=payload.get("stale_threshold_sec"),
        )
        threshold = default_stale_threshold(request.session_key)
        return datasource_dict(ds, threshold), 201
