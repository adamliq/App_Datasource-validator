"""
Accessor for the dsv_settings collection - a small key/value store for
app-wide configuration (e.g. default staleness threshold, dispatch
timeout) that isn't a secret and isn't per-object. Secrets never live
here - see security.py / storage/passwords.
"""
from typing import Optional

from app.models.base import Store, utcnow_iso

DEFAULT_STALE_THRESHOLD_SEC = "default_stale_threshold_sec"
DEFAULT_DISPATCH_TIMEOUT_SEC = "default_dispatch_timeout_sec"
SEARCH_TIME_RANGE_EARLIEST = "search_time_range_earliest"
SEARCH_TIME_RANGE_LATEST = "search_time_range_latest"


class SettingsStore(Store):
    collection_name = "dsv_settings"

    def get(self, setting_id, default=None):
        doc = self._get_raw(setting_id)
        return doc["value"] if doc else default

    def get_all(self) -> dict:
        docs = self._query()
        return {d["setting_id"]: d.get("value") for d in docs}

    def set(self, setting_id, value, updated_by=None):
        existing = self._get_raw(setting_id)
        doc = {
            "_key": setting_id,
            "setting_id": setting_id,
            "value": value,
            "updated_time": utcnow_iso(),
            "updated_by": updated_by,
        }
        if existing:
            self._update(setting_id, doc)
        else:
            self._insert(doc)
        return value

    def delete(self, setting_id):
        self._delete(setting_id)
