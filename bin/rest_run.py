"""
bin/rest_run.py - restmap.conf [script:dsv_run]
(/datasource_validator/run/<id>, capability.get = dsv_view_results,
capability.post/delete = dsv_run_validation).

Get a single run (with its queue items) and cancel it. See
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
from validation_controller import cancel_run  # noqa: E402


class DsvRunHandler(rest_base.DsvPersistentHandler):
    def _run_id(self, request):
        if not request.path_segments:
            raise ValueError("run_id is required in the URL path")
        return request.path_segments[0]

    def handle_get(self, request):
        run_id = self._run_id(request)
        st = stores(request.session_key)
        run = st.run_store.get(run_id)
        if run is None:
            raise NotFoundError(f"no such run: {run_id}")
        items = st.queue_store.list_for_run(run_id)
        return {"run": run_dict(run), "items": [queue_item_dict(i) for i in items]}

    def handle_post(self, request):
        run_id = self._run_id(request)
        st = stores(request.session_key)
        run = cancel_run(st, run_id)
        return run_dict(run)

    def handle_delete(self, request):
        return self.handle_post(request)
