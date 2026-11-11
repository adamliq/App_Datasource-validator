"""
bin/rest_queue.py - restmap.conf [script:dsv_queue]
(/datasource_validator/queue, capability.get = dsv_view_results).

Live queue view: what's currently running, and how deep the queued
backlog is. See app/rest_validation_shared.py for the shared helpers
every platform/data-source/run/queue/results handler uses, and its
docstring for why these handlers are split one-per-file.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import rest_base  # noqa: E402
from app.models.validation_queue import QUEUE_STATUS_QUEUED  # noqa: E402
from app.rest_validation_shared import queue_item_dict, stores  # noqa: E402


class DsvQueueHandler(rest_base.DsvPersistentHandler):
    def handle_get(self, request):
        st = stores(request.session_key)
        run_id = request.query.get("run_id")
        if run_id:
            items = st.queue_store.list_for_run(run_id)
            return {"run_id": run_id, "items": [queue_item_dict(i) for i in items]}

        running = st.queue_store.find_any_running()
        return {
            "running": queue_item_dict(running) if running else None,
            "queued_depth": st.queue_store.count_global_status(QUEUE_STATUS_QUEUED),
        }
