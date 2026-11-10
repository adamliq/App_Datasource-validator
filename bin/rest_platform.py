"""
bin/rest_platform.py - restmap.conf [script:dsv_platform]
(/datasource_validator/platforms/<id>, capability.get = dsv_view_results,
capability.put/post/delete = dsv_manage_datasources).

Get/update/delete a single platform. See app/rest_validation_shared.py
for the shared helpers every platform/data-source/run/queue/results
handler uses, and its docstring for why these handlers are split
one-per-file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app.models.base import NotFoundError  # noqa: E402
from app.rest_validation_shared import (  # noqa: E402
    PLATFORM_UPDATE_FIELDS,
    default_stale_threshold,
    platform_dict,
    stores,
)
from app.status_calculator import compute_datasource_display_status  # noqa: E402


class DsvPlatformHandler(rest_base.DsvPersistentHandler):
    def _platform_id(self, request):
        if not request.path_segments:
            raise ValueError("platform_id is required in the URL path")
        return request.path_segments[0]

    def handle_get(self, request):
        platform_id = self._platform_id(request)
        st = stores(request.session_key)
        platform = st.platform_store.get(platform_id)
        if platform is None:
            raise NotFoundError(f"no such platform: {platform_id}")
        threshold = default_stale_threshold(request.session_key)
        children = [
            compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
            for ds in st.datasource_store.list(platform_id=platform_id)
        ]
        return platform_dict(platform, children)

    def handle_put(self, request):
        return self.handle_post(request)

    def handle_post(self, request):
        platform_id = self._platform_id(request)
        payload = request.payload or {}
        fields = {k: v for k, v in payload.items() if k in PLATFORM_UPDATE_FIELDS}
        st = stores(request.session_key)
        platform = st.platform_store.update(platform_id, **fields)
        threshold = default_stale_threshold(request.session_key)
        children = [
            compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
            for ds in st.datasource_store.list(platform_id=platform_id)
        ]
        return platform_dict(platform, children)

    def handle_delete(self, request):
        platform_id = self._platform_id(request)
        st = stores(request.session_key)
        if st.platform_store.get(platform_id) is None:
            raise NotFoundError(f"no such platform: {platform_id}")
        for ds in st.datasource_store.list(platform_id=platform_id):
            st.datasource_store.delete(ds.datasource_id)
        st.platform_store.delete(platform_id)
        return {"deleted": platform_id}
