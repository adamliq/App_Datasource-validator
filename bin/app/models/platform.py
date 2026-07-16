"""Accessor for the dsv_platforms collection."""
from dataclasses import dataclass
from typing import List, Optional

from app import security
from app.models.base import Store, utcnow_iso


@dataclass
class Platform:
    platform_id: str
    name: str
    description: str = ""
    sort_order: int = 0
    enabled: bool = True
    created_time: Optional[str] = None
    updated_time: Optional[str] = None

    @classmethod
    def from_doc(cls, doc):
        return cls(
            platform_id=doc.get("platform_id", doc.get("_key")),
            name=doc.get("name", ""),
            description=doc.get("description", ""),
            sort_order=int(doc.get("sort_order", 0)),
            enabled=bool(doc.get("enabled", True)),
            created_time=doc.get("created_time"),
            updated_time=doc.get("updated_time"),
        )

    def to_doc(self):
        return {
            "_key": self.platform_id,
            "platform_id": self.platform_id,
            "name": self.name,
            "description": self.description,
            "sort_order": self.sort_order,
            "enabled": self.enabled,
            "created_time": self.created_time,
            "updated_time": self.updated_time,
        }


class PlatformStore(Store):
    collection_name = "dsv_platforms"

    def create(self, platform_id, name, description="", sort_order=0, enabled=True):
        security.validate_identifier(platform_id, "platform_id")
        security.validate_display_text(name, "name", max_length=200)
        security.validate_display_text(description, "description")
        now = utcnow_iso()
        platform = Platform(
            platform_id=platform_id,
            name=name,
            description=description,
            sort_order=int(sort_order),
            enabled=bool(enabled),
            created_time=now,
            updated_time=now,
        )
        self._insert(platform.to_doc())
        return platform

    def get(self, platform_id) -> Optional[Platform]:
        doc = self._get_raw(platform_id)
        return Platform.from_doc(doc) if doc else None

    def list(self, enabled_only=False) -> List[Platform]:
        query = {"enabled": True} if enabled_only else None
        docs = self._query(query=query, sort="sort_order,name")
        return [Platform.from_doc(d) for d in docs]

    def update(self, platform_id, **fields):
        allowed = {"name", "description", "sort_order", "enabled"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update field(s): {sorted(unknown)}")
        if "name" in fields:
            security.validate_display_text(fields["name"], "name", max_length=200)
        if "description" in fields:
            security.validate_display_text(fields["description"], "description")

        current = self._require_raw(platform_id)
        current.update(fields)
        current["updated_time"] = utcnow_iso()
        self._update(platform_id, current)
        return Platform.from_doc(current)

    def delete(self, platform_id):
        self._delete(platform_id)
