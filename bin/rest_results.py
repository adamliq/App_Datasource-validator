"""
bin/rest_results.py - restmap.conf [script:dsv_results]
(/datasource_validator/results, capability.get = dsv_view_results).

Result history for a data source or a run. See
app/rest_validation_shared.py for the shared helpers every platform/
data-source/run/queue/results handler uses, and its docstring for why
these handlers are split one-per-file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app.rest_validation_shared import result_dict, stores  # noqa: E402


class DsvResultsHandler(rest_base.DsvPersistentHandler):
    def handle_get(self, request):
        st = stores(request.session_key)
        datasource_id = request.query.get("datasource_id")
        run_id = request.query.get("run_id")
        limit = int(request.query["limit"]) if request.query.get("limit") else None

        if datasource_id:
            results = st.result_store.list_for_datasource(datasource_id, limit=limit)
        elif run_id:
            results = st.result_store.list_for_run(run_id)
        else:
            raise ValueError("either datasource_id or run_id is required")

        return {"results": [result_dict(r) for r in results]}
