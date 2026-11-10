"""
bin/rest_runs.py - restmap.conf [script:dsv_runs]
(/datasource_validator/run[/<id>], capability.get = dsv_view_results,
capability.post/delete = dsv_run_validation).

List runs and start a new one; get a single run (with its queue items)
and cancel it - in one handler. A single restmap match is used for both
the collection and item paths - see rest_platforms.py's docstring for why
this is one handler rather than two overlapping restmap stanzas. See
app/rest_validation_shared.py for the shared helpers every platform/
data-source/run/queue/results handler uses, and its docstring for why
these handlers are split one-per-file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app.models.base import NotFoundError  # noqa: E402
from app.rest_validation_shared import queue_item_dict, run_dict, stores  # noqa: E402
from validation_controller import cancel_run, create_run  # noqa: E402


class DsvRunsHandler(rest_base.DsvPersistentHandler):
    def handle_get(self, request):
        st = stores(request.session_key)

        if request.path_segments:
            run_id = request.path_segments[0]
            run = st.run_store.get(run_id)
            if run is None:
                raise NotFoundError(f"no such run: {run_id}")
            items = st.queue_store.list_for_run(run_id)
            return {"run": run_dict(run), "items": [queue_item_dict(i) for i in items]}

        status = request.query.get("status")
        limit = int(request.query["limit"]) if request.query.get("limit") else None
        runs = st.run_store.list(status=status, limit=limit)
        return {"runs": [run_dict(r) for r in runs]}

    def handle_post(self, request):
        st = stores(request.session_key)

        if request.path_segments:
            run_id = request.path_segments[0]
            run = cancel_run(st, run_id)
            return run_dict(run)

        payload = request.payload or {}
        datasource_ids = payload.get("datasource_ids")
        run = create_run(
            st, created_by=request.user or "unknown",
            trigger_type=payload.get("trigger_type", "manual"),
            datasource_ids=set(datasource_ids) if datasource_ids else None,
        )
        return run_dict(run), 201

    def handle_delete(self, request):
        if not request.path_segments:
            raise ValueError("run_id is required in the URL path")
        return self.handle_post(request)
