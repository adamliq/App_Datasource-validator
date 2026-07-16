"""
Shared plumbing for KV Store model accessors.

Every Store subclass talks to a duck-typed `collection` client rather than
splunklib directly, so the whole model layer is unit-testable with a plain
in-memory fake (see tests/unit/fakes.py) and has zero network dependency.
Phase 3 adapts the real splunklib.client.KVStoreCollectionData object to
this same small protocol:

    insert(doc: dict) -> dict            # returns the stored doc incl. _key
    get(key: str) -> Optional[dict]      # None if not found
    query(query=None, sort=None, limit=None, skip=None) -> List[dict]
    update(key: str, doc: dict) -> dict
    delete(key: str) -> None
    delete_query(query: dict) -> None
"""
import datetime
import uuid


class NotFoundError(Exception):
    """Raised when an operation requires an existing document that isn't there."""


def utcnow_iso():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    )


def parse_iso(value):
    if not value:
        return None
    text = value[:-1] if value.endswith("Z") else value
    return datetime.datetime.fromisoformat(text).replace(tzinfo=datetime.timezone.utc)


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex}"


class Store:
    collection_name = None

    def __init__(self, collection):
        """
        `collection` is the duck-typed KV Store client described above,
        already scoped to this store's collection (i.e. the caller picked
        collection_name when constructing it, not this class).
        """
        self._collection = collection

    def _insert(self, doc):
        return self._collection.insert(doc)

    def _get_raw(self, key):
        return self._collection.get(key)

    def _require_raw(self, key):
        doc = self._collection.get(key)
        if doc is None:
            raise NotFoundError(f"{self.collection_name}: no document with _key={key!r}")
        return doc

    def _query(self, query=None, sort=None, limit=None, skip=None):
        return self._collection.query(query=query, sort=sort, limit=limit, skip=skip)

    def _update(self, key, doc):
        self._require_raw(key)
        return self._collection.update(key, doc)

    def _delete(self, key):
        self._collection.delete(key)

    def _delete_query(self, query):
        self._collection.delete_query(query)
