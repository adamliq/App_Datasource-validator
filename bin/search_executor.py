"""
Runs one data source's validation search - non-blocking by design.
dispatch() starts a job and returns immediately; poll() is called on a
later worker tick to check progress and, once the job is done, classify
the outcome. This is what makes "exactly one search job in flight"
purely a matter of KV Store queue state (see validation_controller.py)
rather than a blocking wait inside the worker process, which is what
keeps a browser refresh / worker restart safe per HANDOFF.md's
architecture.

Depends only on app.query_validator (already unit-tested against fakes)
and a duck-typed `job_client` with dispatch()/poll()/cancel() methods -
app.splunk_client.SearchJobClient in production, a fake in tests.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.query_validator import QueryValidator  # noqa: E402

DEFAULT_EARLIEST_TIME = "-24h@h"
DEFAULT_LATEST_TIME = "now"
DEFAULT_MAX_TIME_SEC = 120
DEFAULT_STUCK_JOB_TIMEOUT_SEC = 300

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_ERROR = "ERROR"


class DispatchResult:
    def __init__(self, allowed, sid=None, executable_query=None, reason=None):
        self.allowed = allowed
        self.sid = sid
        self.executable_query = executable_query
        self.reason = reason


class ValidationOutcome:
    def __init__(self, status, result_count, error_message=None):
        self.status = status
        self.result_count = result_count
        self.error_message = error_message


class SearchExecutor:
    def __init__(
        self, query_validator: QueryValidator, job_client,
        earliest_time=DEFAULT_EARLIEST_TIME, latest_time=DEFAULT_LATEST_TIME,
        max_time_sec=DEFAULT_MAX_TIME_SEC,
    ):
        self._query_validator = query_validator
        self._job_client = job_client
        self._earliest_time = earliest_time
        self._latest_time = latest_time
        self._max_time_sec = max_time_sec

    def dispatch(self, spl_query, is_admin=False):
        """
        Validates spl_query (fail-closed, see query_validator.py) and, if
        allowed, dispatches it as a bounded-time-range, bounded-duration
        search job. Never executes a query that failed validation.
        """
        validation = self._query_validator.validate(spl_query, is_admin=is_admin)
        if not validation.allowed:
            return DispatchResult(allowed=False, reason=validation.reason)

        try:
            sid = self._job_client.dispatch(
                validation.executable_query, self._earliest_time,
                self._latest_time, self._max_time_sec,
            )
        except Exception as e:
            return DispatchResult(allowed=False, reason=f"dispatch failed: {e}")

        return DispatchResult(allowed=True, sid=sid, executable_query=validation.executable_query)

    def poll(self, sid):
        """Returns None if the job is still running, else a ValidationOutcome."""
        status = self._job_client.poll(sid)
        if not status.get("is_done"):
            return None

        if status.get("is_failed"):
            error_text = _summarize_messages(status.get("messages") or []) or "search job failed"
            return ValidationOutcome(status=STATUS_ERROR, result_count=0, error_message=error_text)

        result_count = int(status.get("result_count") or 0)
        outcome_status = STATUS_PASS if result_count > 0 else STATUS_FAIL
        return ValidationOutcome(status=outcome_status, result_count=result_count)

    def cancel(self, sid):
        self._job_client.cancel(sid)


def _summarize_messages(messages):
    texts = []
    for m in messages:
        if isinstance(m, dict):
            texts.append(str(m.get("text", m)))
        else:
            texts.append(str(m))
    return "; ".join(texts)[:2000]
