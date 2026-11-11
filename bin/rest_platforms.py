"""
bin/rest_platforms.py - restmap.conf [script:dsv_platforms]
(/datasource_validator/platforms[/<id>], capability.get = dsv_view_results,
capability.post/put/delete = dsv_manage_datasources).

List/create platforms, and get/update/delete a single one, in one handler.
A single restmap match is used for both the collection and item paths -
Splunk's persist-connection dispatcher does not disambiguate a bare prefix
match from one with a trailing slash when both are registered (confirmed
live: a bare GET /platforms was being routed to the item-only handler,
which failed with "platform_id is required in the URL path" on every
request). Branching on request.path_segments here, instead of relying on
two overlapping restmap stanzas, is what actually avoids that collision.
See app/rest_validation_shared.py for the shared helpers every platform/
data-source/run/queue/results handler uses, and its docstring for why
these handlers are split one-per-file.
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app import security  # noqa: E402
from app.models.base import NotFoundError  # noqa: E402
from app.rest_validation_shared import (  # noqa: E402
    PLATFORM_UPDATE_FIELDS,
    clean_metadata,
    default_stale_threshold,
    platform_dict,
    stores,
)
from app.status_calculator import compute_datasource_display_status  # noqa: E402


class DsvPlatformsHandler(rest_base.DsvPersistentHandler):
    def _platform_id(self, request):
        if not request.path_segments:
            raise ValueError("platform_id is required in the URL path")
        return request.path_segments[0]

    def handle_get(self, request):
        st = stores(request.session_key)
        threshold = default_stale_threshold(request.session_key)

        if request.path_segments:
            platform_id = self._platform_id(request)
            platform = st.platform_store.get(platform_id)
            if platform is None:
                raise NotFoundError(f"no such platform: {platform_id}")
            children = [
                compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
                for ds in st.datasource_store.list(platform_id=platform_id)
            ]
            return platform_dict(platform, children)

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

    def handle_put(self, request):
        return self.handle_post(request)

    def handle_post(self, request):
        st = stores(request.session_key)
        payload = request.payload or {}

        if request.path_segments:
            platform_id = self._platform_id(request)
            fields = clean_metadata(payload, PLATFORM_UPDATE_FIELDS)
            platform = st.platform_store.update(platform_id, **fields)
            threshold = default_stale_threshold(request.session_key)
            children = [
                compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
                for ds in st.datasource_store.list(platform_id=platform_id)
            ]
            return platform_dict(platform, children)

        platform_id = payload.get("platform_id")
        security.validate_identifier(platform_id or "", "platform_id")
        if st.platform_store.get(platform_id) is not None:
            raise ValueError(f"a platform with the id {platform_id} already exists")

        fields = clean_metadata(payload, PLATFORM_UPDATE_FIELDS)
        platform = st.platform_store.create(
            platform_id=platform_id,
            name=fields.get("name"),
            description=fields.get("description", ""),
            sort_order=fields.get("sort_order", 0),
            enabled=fields.get("enabled", True),
        )
        return platform_dict(platform, []), 201

    def handle_delete(self, request):
        platform_id = self._platform_id(request)
        st = stores(request.session_key)
        if st.platform_store.get(platform_id) is None:
            raise NotFoundError(f"no such platform: {platform_id}")
        for ds in st.datasource_store.list(platform_id=platform_id):
            st.datasource_store.delete(ds.datasource_id)
        st.platform_store.delete(platform_id)
        return {"deleted": platform_id}
