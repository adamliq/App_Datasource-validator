"""
bin/rest_query.py - the setup page / data source editor's "test query"
action (restmap.conf [script:dsv_query_test], capability
dsv_run_validation, POST only).

Per HANDOFF.md's architecture, this runs synchronously and, like every
other search this app ever executes, under the dedicated dsv_service
identity - never the calling user's own session - so "can this query
find data" is always answered with exactly the same privileges the
scheduled validation worker has, not the (possibly much broader)
privileges of whoever is testing it.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# rest_base imported as a module, not `from app.rest_base import
# DsvPersistentHandler` - see bin/rest_config.py's import comment for why
# (a direct import breaks Splunk's persist-connection loader).
from app import rest_base  # noqa: E402
from app import security  # noqa: E402
from app.query_validator import QueryValidator  # noqa: E402
from app.splunk_client import (  # noqa: E402
    SearchJobClient,
    ServiceHandle,
    SplunkRestError,
    SplunkRestSearchParser,
    login,
)
from search_executor import DEFAULT_EARLIEST_TIME, DEFAULT_LATEST_TIME, SearchExecutor

POLL_INTERVAL_SEC = 1.0
MAX_WAIT_SEC = 25
TEST_MAX_TIME_SEC = 30


class DsvQueryTestHandler(rest_base.DsvPersistentHandler):
    def handle_post(self, request):
        payload = request.payload or {}
        spl_query = payload.get("spl_query")
        if not spl_query:
            raise ValueError("spl_query is required")

        service = ServiceHandle(request.session_key)
        credential = security.get_service_credential(service)
        if credential is None:
            raise ValueError(
                "no service account is configured yet - finish setup before testing a query"
            )

        try:
            service_session_key = login(credential["username"], credential["clear_password"])
        except SplunkRestError as e:
            raise ValueError(f"could not authenticate as the service account: {e}")

        parser = SplunkRestSearchParser(service_session_key)
        validator = QueryValidator(parser)
        job_client = SearchJobClient(service_session_key)
        executor = SearchExecutor(
            validator, job_client,
            earliest_time=payload.get("earliest_time", DEFAULT_EARLIEST_TIME),
            latest_time=payload.get("latest_time", DEFAULT_LATEST_TIME),
            max_time_sec=TEST_MAX_TIME_SEC,
        )

        dispatch_result = executor.dispatch(spl_query, is_admin=_is_admin_payload(payload))
        if not dispatch_result.allowed:
            return {"allowed": False, "reason": dispatch_result.reason}

        outcome = self._wait_for_outcome(executor, dispatch_result.sid)
        if outcome is None:
            executor.cancel(dispatch_result.sid)
            return {
                "allowed": True, "status": "TIMEOUT", "result_count": 0,
                "error_message": f"search did not finish within {MAX_WAIT_SEC}s",
            }

        return {
            "allowed": True,
            "status": outcome.status,
            "result_count": outcome.result_count,
            "error_message": outcome.error_message,
        }

    def _wait_for_outcome(self, executor, sid):
        waited = 0.0
        while waited < MAX_WAIT_SEC:
            outcome = executor.poll(sid)
            if outcome is not None:
                return outcome
            time.sleep(POLL_INTERVAL_SEC)
            waited += POLL_INTERVAL_SEC
        return None


def _is_admin_payload(payload):
    # The admin leading-command allowlist is a defense-in-depth concept
    # for saved data sources (see rest_validation.py, which checks the
    # caller's real capabilities); ad-hoc test queries never get that
    # relaxation, regardless of what the client sends.
    return False
