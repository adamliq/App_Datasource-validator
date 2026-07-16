"""Accessor for the dsv_validation_runs collection."""
from dataclasses import dataclass
from typing import List, Optional

from app.models.base import Store, new_id, utcnow_iso

RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_COMPLETED = "COMPLETED"
RUN_STATUS_CANCELLED = "CANCELLED"


@dataclass
class ValidationRun:
    run_id: str
    created_time: str
    created_by: str
    trigger_type: str
    status: str
    total_items: int
    completed_items: int = 0
    completed_time: Optional[str] = None

    @classmethod
    def from_doc(cls, doc):
        return cls(
            run_id=doc.get("run_id", doc.get("_key")),
            created_time=doc.get("created_time"),
            created_by=doc.get("created_by", ""),
            trigger_type=doc.get("trigger_type", "manual"),
            status=doc.get("status", RUN_STATUS_RUNNING),
            total_items=int(doc.get("total_items", 0)),
            completed_items=int(doc.get("completed_items", 0)),
            completed_time=doc.get("completed_time"),
        )

    def to_doc(self):
        return {
            "_key": self.run_id,
            "run_id": self.run_id,
            "created_time": self.created_time,
            "created_by": self.created_by,
            "trigger_type": self.trigger_type,
            "status": self.status,
            "total_items": self.total_items,
            "completed_items": self.completed_items,
            "completed_time": self.completed_time,
        }


class ValidationRunStore(Store):
    collection_name = "dsv_validation_runs"

    def create(self, created_by, trigger_type, total_items, run_id=None):
        run = ValidationRun(
            run_id=run_id or new_id("run"),
            created_time=utcnow_iso(),
            created_by=created_by,
            trigger_type=trigger_type,
            status=RUN_STATUS_RUNNING,
            total_items=int(total_items),
            completed_items=0,
        )
        self._insert(run.to_doc())
        return run

    def get(self, run_id) -> Optional[ValidationRun]:
        doc = self._get_raw(run_id)
        return ValidationRun.from_doc(doc) if doc else None

    def list(self, status=None, limit=None) -> List[ValidationRun]:
        query = {"status": status} if status else None
        docs = self._query(query=query, sort="-created_time", limit=limit)
        return [ValidationRun.from_doc(d) for d in docs]

    def set_progress(self, run_id, completed_items):
        current = self._require_raw(run_id)
        current["completed_items"] = int(completed_items)
        self._update(run_id, current)
        return ValidationRun.from_doc(current)

    def mark_completed(self, run_id):
        current = self._require_raw(run_id)
        current["status"] = RUN_STATUS_COMPLETED
        current["completed_time"] = utcnow_iso()
        self._update(run_id, current)
        return ValidationRun.from_doc(current)

    def cancel(self, run_id):
        current = self._require_raw(run_id)
        current["status"] = RUN_STATUS_CANCELLED
        current["completed_time"] = utcnow_iso()
        self._update(run_id, current)
        return ValidationRun.from_doc(current)
