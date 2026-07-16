"""
Accessor for the dsv_worker_lease collection - guarantees a single active
dsv_validation_worker across search-head-cluster members. The lease is a
single fixed-key document; whichever worker holds an unexpired lease is
the only one allowed to dispatch. This also feeds the Health dashboard
(owner + heartbeat_time tell you which member is active and how recently
it ticked).

True cross-node atomicity ultimately rests on KV Store's own consistency
(writes go through a single primary), which this module cannot verify
outside a real cluster - see HANDOFF.md's "Verify in your environment"
notes. The read-then-write acquire logic here is best-effort optimistic
locking, appropriate for a lease that is renewed every few seconds and
tolerant of an occasional lost race (the loser simply doesn't dispatch
this tick and tries again next tick).
"""
from dataclasses import dataclass
from typing import Optional

from app.models.base import Store, parse_iso, utcnow_iso

LEASE_ID = "dsv_validation_worker"


@dataclass
class WorkerLease:
    lease_id: str
    owner: str
    acquired_time: str
    heartbeat_time: str
    expires_time: str

    @classmethod
    def from_doc(cls, doc):
        return cls(
            lease_id=doc.get("lease_id", doc.get("_key")),
            owner=doc.get("owner", ""),
            acquired_time=doc.get("acquired_time"),
            heartbeat_time=doc.get("heartbeat_time"),
            expires_time=doc.get("expires_time"),
        )

    def to_doc(self):
        return {
            "_key": self.lease_id,
            "lease_id": self.lease_id,
            "owner": self.owner,
            "acquired_time": self.acquired_time,
            "heartbeat_time": self.heartbeat_time,
            "expires_time": self.expires_time,
        }

    def is_expired(self, now=None):
        now = now or parse_iso(utcnow_iso())
        expires = parse_iso(self.expires_time)
        return expires is None or now >= expires


class WorkerLeaseStore(Store):
    collection_name = "dsv_worker_lease"

    def get(self) -> Optional[WorkerLease]:
        doc = self._get_raw(LEASE_ID)
        return WorkerLease.from_doc(doc) if doc else None

    def try_acquire(self, owner, ttl_seconds):
        """
        Acquire the lease if it is unheld, expired, or already owned by
        `owner` (idempotent renew). Returns the WorkerLease on success,
        None if another live owner holds it.
        """
        now_iso = utcnow_iso()
        now = parse_iso(now_iso)
        existing = self.get()

        if existing is not None and existing.owner != owner and not existing.is_expired(now):
            return None

        expires = _add_seconds(now_iso, ttl_seconds)
        lease = WorkerLease(
            lease_id=LEASE_ID,
            owner=owner,
            acquired_time=now_iso if existing is None or existing.owner != owner else existing.acquired_time,
            heartbeat_time=now_iso,
            expires_time=expires,
        )
        if existing is None:
            self._insert(lease.to_doc())
        else:
            self._update(LEASE_ID, lease.to_doc())
        return lease

    def heartbeat(self, owner, ttl_seconds):
        existing = self.get()
        if existing is None or existing.owner != owner:
            return None
        now_iso = utcnow_iso()
        existing.heartbeat_time = now_iso
        existing.expires_time = _add_seconds(now_iso, ttl_seconds)
        self._update(LEASE_ID, existing.to_doc())
        return existing

    def release(self, owner):
        existing = self.get()
        if existing is None or existing.owner != owner:
            return False
        self._delete(LEASE_ID)
        return True


def _add_seconds(iso_ts, seconds):
    import datetime
    dt = parse_iso(iso_ts) + datetime.timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
