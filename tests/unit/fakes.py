"""
In-memory fakes for the duck-typed Splunk interfaces app/* depends on, so
unit tests exercise real business logic with zero network access and no
Splunk installation.
"""
import copy


class FakeKVCollection:
    """Implements the Store protocol: insert/get/query/update/delete/delete_query."""

    def __init__(self):
        self._docs = {}

    def insert(self, doc):
        doc = copy.deepcopy(doc)
        key = doc.get("_key")
        if key is None:
            raise ValueError("fake collection requires an explicit _key")
        if key in self._docs:
            raise ValueError(f"duplicate _key: {key}")
        self._docs[key] = doc
        return copy.deepcopy(doc)

    def get(self, key):
        doc = self._docs.get(key)
        return copy.deepcopy(doc) if doc is not None else None

    def query(self, query=None, sort=None, limit=None, skip=None):
        docs = list(self._docs.values())
        if query:
            docs = [d for d in docs if all(d.get(k) == v for k, v in query.items())]
        if sort:
            for field_spec in reversed([f for f in sort.split(",") if f.strip()]):
                field_spec = field_spec.strip()
                desc = field_spec.startswith("-")
                field = field_spec[1:] if desc else field_spec
                docs.sort(key=lambda d: _sort_key(d.get(field)), reverse=desc)
        if skip:
            docs = docs[skip:]
        if limit:
            docs = docs[:limit]
        return [copy.deepcopy(d) for d in docs]

    def update(self, key, doc):
        if key not in self._docs:
            raise KeyError(key)
        doc = copy.deepcopy(doc)
        doc["_key"] = key
        self._docs[key] = doc
        return copy.deepcopy(doc)

    def delete(self, key):
        self._docs.pop(key, None)

    def delete_query(self, query):
        to_delete = [
            k for k, d in self._docs.items()
            if all(d.get(qk) == qv for qk, qv in query.items())
        ]
        for k in to_delete:
            del self._docs[k]


def _sort_key(value):
    return (value is None, value if value is not None else "")


class FakeStoragePasswordEntry:
    def __init__(self, name, username, clear_password, realm):
        self.name = name
        self.username = username
        self.clear_password = clear_password
        self.realm = realm


class FakeStoragePasswords:
    def __init__(self):
        self._entries = []

    def __iter__(self):
        return iter(list(self._entries))

    def create(self, password, username=None, realm=None):
        entry = FakeStoragePasswordEntry(
            name=f"{realm}:{username}:", username=username,
            clear_password=password, realm=realm,
        )
        self._entries.append(entry)
        return entry

    def delete(self, name):
        self._entries = [e for e in self._entries if e.name != name]


class FakeService:
    def __init__(self):
        self.storage_passwords = FakeStoragePasswords()


class FakeSearchParser:
    """
    Maps exact query strings to a canned ParsedSearch (or an exception
    instance/class to raise), so tests can control parser behavior
    precisely without any real SPL parsing or network call.
    """

    def __init__(self):
        self._responses = {}

    def stub(self, query, parsed_or_exception):
        self._responses[query] = parsed_or_exception

    def parse(self, spl_query):
        response = self._responses.get(spl_query)
        if response is None:
            raise AssertionError(f"FakeSearchParser has no stub for: {spl_query!r}")
        if isinstance(response, Exception):
            raise response
        if isinstance(response, type) and issubclass(response, Exception):
            raise response("stubbed failure")
        return response


class FakeSearchJobClient:
    """Fake for app.splunk_client.SearchJobClient, used by SearchExecutor tests."""

    def __init__(self):
        self.dispatch_calls = []
        self.cancelled = []
        self._jobs = {}
        self._next_sid = 1

    def dispatch(self, executable_query, earliest_time, latest_time, max_time_sec):
        sid = f"sid_{self._next_sid}"
        self._next_sid += 1
        self.dispatch_calls.append(
            {"query": executable_query, "earliest_time": earliest_time,
             "latest_time": latest_time, "max_time_sec": max_time_sec}
        )
        self._jobs[sid] = {"is_done": False, "is_failed": False, "result_count": 0, "messages": []}
        return sid

    def poll(self, sid):
        return dict(self._jobs[sid])

    def cancel(self, sid):
        self.cancelled.append(sid)
        if sid in self._jobs:
            self._jobs[sid]["is_done"] = True

    def set_job_state(self, sid, is_done=True, is_failed=False, result_count=0, messages=None):
        self._jobs[sid] = {
            "is_done": is_done, "is_failed": is_failed,
            "result_count": result_count, "messages": messages or [],
        }


class FakeDispatchResult:
    def __init__(self, allowed, sid=None, reason=None, executable_query=None):
        self.allowed = allowed
        self.sid = sid
        self.reason = reason
        self.executable_query = executable_query


class FakeValidationOutcome:
    def __init__(self, status, result_count, error_message=None):
        self.status = status
        self.result_count = result_count
        self.error_message = error_message


class FakeSearchExecutor:
    """
    Fake for search_executor.SearchExecutor, used by validation_controller
    tests so controller branching (lease handling, dispatch, finalize,
    run completion, stuck-job timeout, rejected queries) can be exercised
    without routing every scenario through real query validation.
    """

    def __init__(self):
        self.dispatch_calls = []
        self.dispatch_queue = []  # FakeDispatchResult objects, consumed in order
        self.poll_results = {}    # sid -> list of (outcome-or-None), consumed in order
        self.cancelled = []
        self._next_sid = 1

    def dispatch(self, spl_query, is_admin=False):
        self.dispatch_calls.append(spl_query)
        if self.dispatch_queue:
            return self.dispatch_queue.pop(0)
        sid = f"sid_{self._next_sid}"
        self._next_sid += 1
        return FakeDispatchResult(allowed=True, sid=sid, executable_query=spl_query)

    def poll(self, sid):
        queue = self.poll_results.get(sid, [])
        if not queue:
            return None
        return queue.pop(0)

    def cancel(self, sid):
        self.cancelled.append(sid)
