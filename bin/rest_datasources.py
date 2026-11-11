"""
bin/rest_datasources.py - restmap.conf [script:dsv_datasources]
(/datasource_validator/datasources[/<id>], capability.get =
dsv_view_results, capability.post/put/delete = dsv_manage_datasources).

List/create data sources, and get/update/delete a single one, in one
handler. A single restmap match is used for both the collection and item
paths - see rest_platforms.py's docstring for why this is one handler
rather than two overlapping restmap stanzas. See
app/rest_validation_shared.py for the shared helpers every platform/
data-source/run/queue/results handler uses, and its docstring for why
these handlers are split one-per-file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app import security  # noqa: E402
from app.models.base import NotFoundError  # noqa: E402
from app.rest_validation_shared import (  # noqa: E402
    DATASOURCE_METADATA_FIELDS,
    clean_metadata,
    datasource_dict,
    default_stale_threshold,
    is_admin,
    stores,
    validate_query_or_raise,
)


class DsvDatasourcesHandler(rest_base.DsvPersistentHandler):
    def _datasource_id(self, request):
        if not request.path_segments:
            raise ValueError("datasource_id is required in the URL path")
        return request.path_segments[0]

    def handle_get(self, request):
        st = stores(request.session_key)
        threshold = default_stale_threshold(request.session_key)

        if request.path_segments:
            datasource_id = self._datasource_id(request)
            ds = st.datasource_store.get(datasource_id)
            if ds is None:
                raise NotFoundError(f"no such data source: {datasource_id}")
            return datasource_dict(ds, threshold)

        platform_id = request.query.get("platform_id")
        datasources = st.datasource_store.list(platform_id=platform_id)
        return {"datasources": [datasource_dict(ds, threshold) for ds in datasources]}

    def handle_put(self, request):
        return self.handle_post(request)

    def handle_post(self, request):
        st = stores(request.session_key)
        payload = request.payload or {}

        if request.path_segments:
            datasource_id = self._datasource_id(request)
            existing = st.datasource_store.get(datasource_id)
            if existing is None:
                raise NotFoundError(f"no such data source: {datasource_id}")

            metadata_fields = clean_metadata(payload, DATASOURCE_METADATA_FIELDS)

            # Re-saving an unchanged query must not reset the data source
            # to NOT RUN, which is what update_query() does by design.
            new_query = payload.get("spl_query")
            if new_query and new_query != existing.spl_query:
                validate_query_or_raise(request.session_key, new_query, is_admin(request.session_key))
                st.datasource_store.update_query(datasource_id, new_query)

            if metadata_fields:
                st.datasource_store.update_metadata(datasource_id, **metadata_fields)

            ds = st.datasource_store.get(datasource_id)
            threshold = default_stale_threshold(request.session_key)
            return datasource_dict(ds, threshold)

        spl_query = payload.get("spl_query")
        if not spl_query:
            raise ValueError("spl_query is required")
        validate_query_or_raise(request.session_key, spl_query, is_admin(request.session_key))

        platform_id = payload.get("platform_id")
        security.validate_identifier(platform_id or "", "platform_id")
        if st.platform_store.get(platform_id) is None:
            raise NotFoundError(f"no such platform: {platform_id}")

        datasource_id = payload.get("datasource_id")
        security.validate_identifier(datasource_id or "", "datasource_id")
        if st.datasource_store.get(datasource_id) is not None:
            raise ValueError(f"a data source with the id {datasource_id} already exists")

        fields = clean_metadata(payload, DATASOURCE_METADATA_FIELDS)
        ds = st.datasource_store.create(
            datasource_id=datasource_id,
            platform_id=platform_id,
            name=fields.get("name"),
            spl_query=spl_query,
            description=fields.get("description", ""),
            sort_order=fields.get("sort_order", 0),
            enabled=fields.get("enabled", True),
            stale_threshold_sec=fields.get("stale_threshold_sec"),
        )
        threshold = default_stale_threshold(request.session_key)
        return datasource_dict(ds, threshold), 201

    def handle_delete(self, request):
        datasource_id = self._datasource_id(request)
        st = stores(request.session_key)
        if st.datasource_store.get(datasource_id) is None:
            raise NotFoundError(f"no such data source: {datasource_id}")
        st.datasource_store.delete(datasource_id)
        return {"deleted": datasource_id}
