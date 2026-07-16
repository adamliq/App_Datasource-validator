"""Accessor for the dsv_datasources collection."""
from dataclasses import dataclass
from typing import List, Optional

from app import security
from app.query_validator import compute_query_hash
from app.models.base import Store, utcnow_iso

STATUS_NOT_RUN = "NOT RUN"
STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_ERROR = "ERROR"
STATUS_CANCELLED = "CANCELLED"

TERMINAL_STATUSES = {STATUS_PASS, STATUS_FAIL, STATUS_ERROR, STATUS_CANCELLED}


@dataclass
class DataSource:
    datasource_id: str
    platform_id: str
    name: str
    spl_query: str
    description: str = ""
    query_hash: str = ""
    query_version: int = 1
    sort_order: int = 0
    enabled: bool = True
    stale_threshold_sec: Optional[int] = None
    current_status: str = STATUS_NOT_RUN
    last_result_time: Optional[str] = None
    last_run_id: Optional[str] = None
    created_time: Optional[str] = None
    updated_time: Optional[str] = None

    @classmethod
    def from_doc(cls, doc):
        return cls(
            datasource_id=doc.get("datasource_id", doc.get("_key")),
            platform_id=doc.get("platform_id", ""),
            name=doc.get("name", ""),
            spl_query=doc.get("spl_query", ""),
            description=doc.get("description", ""),
            query_hash=doc.get("query_hash", ""),
            query_version=int(doc.get("query_version", 1)),
            sort_order=int(doc.get("sort_order", 0)),
            enabled=bool(doc.get("enabled", True)),
            stale_threshold_sec=doc.get("stale_threshold_sec"),
            current_status=doc.get("current_status", STATUS_NOT_RUN),
            last_result_time=doc.get("last_result_time"),
            last_run_id=doc.get("last_run_id"),
            created_time=doc.get("created_time"),
            updated_time=doc.get("updated_time"),
        )

    def to_doc(self):
        return {
            "_key": self.datasource_id,
            "datasource_id": self.datasource_id,
            "platform_id": self.platform_id,
            "name": self.name,
            "description": self.description,
            "spl_query": self.spl_query,
            "query_hash": self.query_hash,
            "query_version": self.query_version,
            "sort_order": self.sort_order,
            "enabled": self.enabled,
            "stale_threshold_sec": self.stale_threshold_sec,
            "current_status": self.current_status,
            "last_result_time": self.last_result_time,
            "last_run_id": self.last_run_id,
            "created_time": self.created_time,
            "updated_time": self.updated_time,
        }


class DataSourceStore(Store):
    collection_name = "dsv_datasources"

    def create(
        self, datasource_id, platform_id, name, spl_query,
        description="", sort_order=0, enabled=True, stale_threshold_sec=None,
    ):
        security.validate_identifier(datasource_id, "datasource_id")
        security.validate_identifier(platform_id, "platform_id")
        security.validate_display_text(name, "name", max_length=200)
        security.validate_display_text(description, "description")
        if not spl_query or not spl_query.strip():
            raise ValueError("spl_query must not be empty")

        now = utcnow_iso()
        ds = DataSource(
            datasource_id=datasource_id,
            platform_id=platform_id,
            name=name,
            spl_query=spl_query,
            description=description,
            query_hash=compute_query_hash(spl_query),
            query_version=1,
            sort_order=int(sort_order),
            enabled=bool(enabled),
            stale_threshold_sec=stale_threshold_sec,
            current_status=STATUS_NOT_RUN,
            created_time=now,
            updated_time=now,
        )
        self._insert(ds.to_doc())
        return ds

    def get(self, datasource_id) -> Optional[DataSource]:
        doc = self._get_raw(datasource_id)
        return DataSource.from_doc(doc) if doc else None

    def list(self, platform_id=None, enabled_only=False) -> List[DataSource]:
        query = {}
        if platform_id is not None:
            query["platform_id"] = platform_id
        if enabled_only:
            query["enabled"] = True
        docs = self._query(query=query or None, sort="sort_order,name")
        return [DataSource.from_doc(d) for d in docs]

    def update_metadata(self, datasource_id, **fields):
        """Update non-query, non-status fields (name/description/sort_order/enabled/stale_threshold_sec)."""
        allowed = {"name", "description", "sort_order", "enabled", "stale_threshold_sec"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update field(s): {sorted(unknown)}")
        if "name" in fields:
            security.validate_display_text(fields["name"], "name", max_length=200)
        if "description" in fields:
            security.validate_display_text(fields["description"], "description")

        current = self._require_raw(datasource_id)
        current.update(fields)
        current["updated_time"] = utcnow_iso()
        self._update(datasource_id, current)
        return DataSource.from_doc(current)

    def update_query(self, datasource_id, new_spl_query):
        """
        Editing a query bumps query_version + query_hash and resets
        current_status to NOT RUN, per HANDOFF's status rules: a stale
        green must not validate new SPL.
        """
        if not new_spl_query or not new_spl_query.strip():
            raise ValueError("spl_query must not be empty")

        current = self._require_raw(datasource_id)
        current["spl_query"] = new_spl_query
        current["query_hash"] = compute_query_hash(new_spl_query)
        current["query_version"] = int(current.get("query_version", 1)) + 1
        current["current_status"] = STATUS_NOT_RUN
        current["updated_time"] = utcnow_iso()
        self._update(datasource_id, current)
        return DataSource.from_doc(current)

    def update_status(self, datasource_id, status, last_result_time=None, last_run_id=None):
        current = self._require_raw(datasource_id)
        current["current_status"] = status
        if last_result_time is not None:
            current["last_result_time"] = last_result_time
        if last_run_id is not None:
            current["last_run_id"] = last_run_id
        current["updated_time"] = utcnow_iso()
        self._update(datasource_id, current)
        return DataSource.from_doc(current)

    def delete(self, datasource_id):
        self._delete(datasource_id)
