"""
bin/rest_datasource.py - restmap.conf [script:dsv_datasource]
(/datasource_validator/datasources/<id>, capability.get =
dsv_view_results, capability.put/post/delete = dsv_manage_datasources).

Get/update/delete a single data source. See app/rest_validation_shared.py
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
    DATASOURCE_METADATA_FIELDS,
    datasource_dict,
    default_stale_threshold,
    is_admin,
    stores,
    validate_query_or_raise,
)


class DsvDatasourceHandler(rest_base.DsvPersistentHandler):
    def _datasource_id(self, request):
        if not request.path_segments:
            raise ValueError("datasource_id is required in the URL path")
        return request.path_segments[0]

    def handle_get(self, request):
        datasource_id = self._datasource_id(request)
        st = stores(request.session_key)
        ds = st.datasource_store.get(datasource_id)
        if ds is None:
            raise NotFoundError(f"no such data source: {datasource_id}")
        threshold = default_stale_threshold(request.session_key)
        return datasource_dict(ds, threshold)

    def handle_put(self, request):
        return self.handle_post(request)

    def handle_post(self, request):
        datasource_id = self._datasource_id(request)
        payload = request.payload or {}
        st = stores(request.session_key)

        if st.datasource_store.get(datasource_id) is None:
            raise NotFoundError(f"no such data source: {datasource_id}")

        if "spl_query" in payload and payload["spl_query"]:
            validate_query_or_raise(request.session_key, payload["spl_query"], is_admin(request.session_key))
            st.datasource_store.update_query(datasource_id, payload["spl_query"])

        metadata_fields = {k: v for k, v in payload.items() if k in DATASOURCE_METADATA_FIELDS}
        if metadata_fields:
            st.datasource_store.update_metadata(datasource_id, **metadata_fields)

        ds = st.datasource_store.get(datasource_id)
        threshold = default_stale_threshold(request.session_key)
        return datasource_dict(ds, threshold)

    def handle_delete(self, request):
        datasource_id = self._datasource_id(request)
        st = stores(request.session_key)
        if st.datasource_store.get(datasource_id) is None:
            raise NotFoundError(f"no such data source: {datasource_id}")
        st.datasource_store.delete(datasource_id)
        return {"deleted": datasource_id}
