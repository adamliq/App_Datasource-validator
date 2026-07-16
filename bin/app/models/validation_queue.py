"""
Accessor for the dsv_validation_queue collection - the ordered work list a
single worker tick consumes. Ordering: run creation order -> platform
sort_order -> data source sort_order -> name is established by the caller
(the worker, Phase 3) when it assigns order_index at run-creation time;
this store just persists and queries it.
"""
from dataclasses import dataclass
from typing import List, Optional

from app.models.base import Store, new_id, utcnow_iso

QUEUE_STATUS_QUEUED = "QUEUED"
QUEUE_STATUS_RUNNING = "RUNNING"
QUEUE_STATUS_DONE = "DONE"
QUEUE_STATUS_ERROR = "ERROR"
QUEUE_STATUS_CANCELLED = "CANCELLED"


@dataclass
class QueueItem:
    queue_id: str
    run_id: str
    platform_id: str
    datasource_id: str
    order_index: int
    status: str
    query_hash: str
    query_version: int
    sid: Optional[str] = None
    dispatched_time: Optional[str] = None
    completed_time: Optional[str] = None

    @classmethod
    def from_doc(cls, doc):
        return cls(
            queue_id=doc.get("queue_id", doc.get("_key")),
            run_id=doc.get("run_id", ""),
            platform_id=doc.get("platform_id", ""),
            datasource_id=doc.get("datasource_id", ""),
            order_index=int(doc.get("order_index", 0)),
            status=doc.get("status", QUEUE_STATUS_QUEUED),
            query_hash=doc.get("query_hash", ""),
            query_version=int(doc.get("query_version", 1)),
            sid=doc.get("sid"),
            dispatched_time=doc.get("dispatched_time"),
            completed_time=doc.get("completed_time"),
        )

    def to_doc(self):
        return {
            "_key": self.queue_id,
            "queue_id": self.queue_id,
            "run_id": self.run_id,
            "platform_id": self.platform_id,
            "datasource_id": self.datasource_id,
            "order_index": self.order_index,
            "status": self.status,
            "query_hash": self.query_hash,
            "query_version": self.query_version,
            "sid": self.sid,
            "dispatched_time": self.dispatched_time,
            "completed_time": self.completed_time,
        }


class ValidationQueueStore(Store):
    collection_name = "dsv_validation_queue"

    def create_batch(self, run_id, items):
        """
        items: iterable of dicts with platform_id, datasource_id,
        order_index, query_hash, query_version. Each carries a unique
        _key so a duplicate dispatch attempt can never execute twice.
        Returns the created QueueItem list, in order_index order.
        """
        created = []
        for item in sorted(items, key=lambda i: i["order_index"]):
            qi = QueueItem(
                queue_id=new_id("queue"),
                run_id=run_id,
                platform_id=item["platform_id"],
                datasource_id=item["datasource_id"],
                order_index=item["order_index"],
                status=QUEUE_STATUS_QUEUED,
                query_hash=item["query_hash"],
                query_version=item["query_version"],
            )
            self._insert(qi.to_doc())
            created.append(qi)
        return created

    def get(self, queue_id) -> Optional[QueueItem]:
        doc = self._get_raw(queue_id)
        return QueueItem.from_doc(doc) if doc else None

    def list_for_run(self, run_id, status=None) -> List[QueueItem]:
        query = {"run_id": run_id}
        if status:
            query["status"] = status
        docs = self._query(query=query, sort="order_index")
        return [QueueItem.from_doc(d) for d in docs]

    def find_any_running(self) -> Optional[QueueItem]:
        """
        The single global "is a search job in flight" check the worker
        makes every tick before it will dispatch another one.
        """
        docs = self._query(query={"status": QUEUE_STATUS_RUNNING}, limit=1)
        return QueueItem.from_doc(docs[0]) if docs else None

    def count_global_status(self, status) -> int:
        """Global count across every run - used by the Health dashboard to show queue depth."""
        return len(self._query(query={"status": status}))

    def mark_running(self, queue_id, sid):
        current = self._require_raw(queue_id)
        current["status"] = QUEUE_STATUS_RUNNING
        current["sid"] = sid
        current["dispatched_time"] = utcnow_iso()
        self._update(queue_id, current)
        return QueueItem.from_doc(current)

    def mark_done(self, queue_id, status=QUEUE_STATUS_DONE):
        current = self._require_raw(queue_id)
        current["status"] = status
        current["completed_time"] = utcnow_iso()
        self._update(queue_id, current)
        return QueueItem.from_doc(current)

    def cancel_queued_for_run(self, run_id):
        """Cancel every item that hasn't started yet for a run being cancelled."""
        for item in self.list_for_run(run_id, status=QUEUE_STATUS_QUEUED):
            self.mark_done(item.queue_id, status=QUEUE_STATUS_CANCELLED)
