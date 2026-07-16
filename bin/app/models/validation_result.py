"""Accessor for the dsv_validation_results collection."""
from dataclasses import dataclass
from typing import List, Optional

from app.models.base import Store, new_id, utcnow_iso


@dataclass
class ValidationResult:
    result_id: str
    run_id: str
    queue_id: str
    datasource_id: str
    platform_id: str
    status: str
    result_count: int
    query_hash: str
    query_version: int
    executed_time: str
    duration_ms: int = 0
    error_message: Optional[str] = None

    @classmethod
    def from_doc(cls, doc):
        return cls(
            result_id=doc.get("result_id", doc.get("_key")),
            run_id=doc.get("run_id", ""),
            queue_id=doc.get("queue_id", ""),
            datasource_id=doc.get("datasource_id", ""),
            platform_id=doc.get("platform_id", ""),
            status=doc.get("status", ""),
            result_count=int(doc.get("result_count", 0)),
            query_hash=doc.get("query_hash", ""),
            query_version=int(doc.get("query_version", 1)),
            executed_time=doc.get("executed_time"),
            duration_ms=int(doc.get("duration_ms", 0)),
            error_message=doc.get("error_message"),
        )

    def to_doc(self):
        return {
            "_key": self.result_id,
            "result_id": self.result_id,
            "run_id": self.run_id,
            "queue_id": self.queue_id,
            "datasource_id": self.datasource_id,
            "platform_id": self.platform_id,
            "status": self.status,
            "result_count": self.result_count,
            "query_hash": self.query_hash,
            "query_version": self.query_version,
            "executed_time": self.executed_time,
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
        }


class ValidationResultStore(Store):
    collection_name = "dsv_validation_results"

    def create(
        self, run_id, queue_id, datasource_id, platform_id, status,
        result_count, query_hash, query_version, duration_ms=0,
        error_message=None, executed_time=None,
    ):
        result = ValidationResult(
            result_id=new_id("result"),
            run_id=run_id,
            queue_id=queue_id,
            datasource_id=datasource_id,
            platform_id=platform_id,
            status=status,
            result_count=int(result_count),
            query_hash=query_hash,
            query_version=int(query_version),
            executed_time=executed_time or utcnow_iso(),
            duration_ms=int(duration_ms),
            error_message=error_message,
        )
        self._insert(result.to_doc())
        return result

    def get(self, result_id) -> Optional[ValidationResult]:
        doc = self._get_raw(result_id)
        return ValidationResult.from_doc(doc) if doc else None

    def latest_for_datasource(self, datasource_id) -> Optional[ValidationResult]:
        docs = self._query(
            query={"datasource_id": datasource_id},
            sort="-executed_time",
            limit=1,
        )
        return ValidationResult.from_doc(docs[0]) if docs else None

    def list_for_datasource(self, datasource_id, limit=None) -> List[ValidationResult]:
        docs = self._query(
            query={"datasource_id": datasource_id},
            sort="-executed_time",
            limit=limit,
        )
        return [ValidationResult.from_doc(d) for d in docs]

    def list_for_run(self, run_id) -> List[ValidationResult]:
        docs = self._query(query={"run_id": run_id}, sort="executed_time")
        return [ValidationResult.from_doc(d) for d in docs]
