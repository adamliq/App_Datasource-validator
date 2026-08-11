"""
bin/rest_health.py - backend for the Health dashboard (restmap.conf
[script:dsv_health], capability dsv_view_results, GET only).

Reports the KV Store worker lease (which search-head-cluster member is
currently the active dispatcher, and how recently it ticked) plus a
quick view of whether anything is stuck in the queue, so an admin can
tell "is the single dispatcher actually alive" at a glance.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models.base import parse_iso, utcnow_iso  # noqa: E402
from app.models.validation_queue import QUEUE_STATUS_QUEUED, ValidationQueueStore  # noqa: E402
from app.models.worker_lease import WorkerLeaseStore  # noqa: E402
# rest_base imported as a module, not `from app.rest_base import
# DsvPersistentHandler` - see bin/rest_config.py's import comment for why
# (a direct import breaks Splunk's persist-connection loader).
from app import rest_base  # noqa: E402
from app.splunk_client import kv_store  # noqa: E402


class DsvHealthHandler(rest_base.DsvPersistentHandler):
    def handle_get(self, request):
        lease_store = WorkerLeaseStore(kv_store(request.session_key, WorkerLeaseStore.collection_name))
        queue_store = ValidationQueueStore(kv_store(request.session_key, ValidationQueueStore.collection_name))

        lease = lease_store.get()
        now = parse_iso(utcnow_iso())

        lease_info = None
        if lease is not None:
            lease_info = {
                "owner": lease.owner,
                "acquired_time": lease.acquired_time,
                "heartbeat_time": lease.heartbeat_time,
                "expires_time": lease.expires_time,
                "expired": lease.is_expired(now),
            }

        running = queue_store.find_any_running()
        queued_depth = queue_store.count_global_status(QUEUE_STATUS_QUEUED)

        return {
            "worker_lease": lease_info,
            "worker_alive": lease_info is not None and not lease_info["expired"],
            "current_running_queue_id": running.queue_id if running else None,
            "current_running_sid": running.sid if running else None,
            "queued_depth": queued_depth,
        }
