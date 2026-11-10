"""
bin/rest_runs.py - restmap.conf [script:dsv_runs]
(/datasource_validator/run, capability.get = dsv_view_results,
capability.post = dsv_run_validation).

List runs and start a new one. See app/rest_validation_shared.py for the
shared helpers every platform/data-source/run/queue/results handler
uses, and its docstring for why these handlers are split one-per-file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app.rest_validation_shared import run_dict, stores  # noqa: E402
from validation_controller import create_run  # noqa: E402


class DsvRunsHandler(rest_base.DsvPersistentHandler):
    def handle_get(self, request):
        st = stores(request.session_key)
        status = request.query.get("status")
        limit = int(request.query["limit"]) if request.query.get("limit") else None
        runs = st.run_store.list(status=status, limit=limit)
        return {"runs": [run_dict(r) for r in runs]}

    def handle_post(self, request):
        payload = request.payload or {}
        st = stores(request.session_key)
        datasource_ids = payload.get("datasource_ids")
        run = create_run(
            st, created_by=request.user or "unknown",
            trigger_type=payload.get("trigger_type", "manual"),
            datasource_ids=set(datasource_ids) if datasource_ids else None,
        )
        return run_dict(run), 201
