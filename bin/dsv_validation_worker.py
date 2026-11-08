#!/usr/bin/env python3
"""
The modular input script Splunk actually executes for the
[dsv_validation_worker://...] stanza in inputs.conf. Deliberately thin:
handles the modular input protocol by hand (--scheme introspection,
stdin configuration XML) so the app needs no vendored SDK, and delegates
every bit of real logic to validation_controller.run_tick(), which
already has full unit test coverage against fakes.

Splunk invokes this script once every `interval` seconds (per
inputs.conf) and lets it exit after each run - the interval scheduling
is splunkd's job, not this script's, so there is deliberately no
internal sleep loop here. That, plus the KV Store lease
(app.models.worker_lease), is what keeps "exactly one search job in
flight" true even across search-head-cluster members.
"""
import socket
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import security  # noqa: E402
from app.logging_utils import get_logger  # noqa: E402
from app.models.datasource import DataSourceStore  # noqa: E402
from app.models.platform import PlatformStore  # noqa: E402
from app.models.settings import (  # noqa: E402
    DEFAULT_DISPATCH_TIMEOUT_SEC,
    SEARCH_TIME_RANGE_EARLIEST,
    SEARCH_TIME_RANGE_LATEST,
    SettingsStore,
)
from app.models.validation_queue import ValidationQueueStore  # noqa: E402
from app.models.validation_result import ValidationResultStore  # noqa: E402
from app.models.validation_run import ValidationRunStore  # noqa: E402
from app.models.worker_lease import WorkerLeaseStore  # noqa: E402
from app.query_validator import QueryValidator  # noqa: E402
from app.splunk_client import (  # noqa: E402
    SearchJobClient,
    ServiceHandle,
    SplunkRestError,
    SplunkRestSearchParser,
    kv_store,
    login,
)
from search_executor import (  # noqa: E402
    DEFAULT_EARLIEST_TIME,
    DEFAULT_LATEST_TIME,
    DEFAULT_MAX_TIME_SEC,
    DEFAULT_STUCK_JOB_TIMEOUT_SEC,
    SearchExecutor,
)
from validation_controller import Stores, run_tick  # noqa: E402

LEASE_TTL_SEC = 30

SCHEME_XML = """<scheme>
  <title>Data Source Validator - Validation Worker</title>
  <description>Single sequential dispatcher for data source validation searches. This input is managed by the app's setup page - do not add a second stanza, and do not run more than one.</description>
  <use_external_validation>false</use_external_validation>
  <use_single_instance>true</use_single_instance>
  <streaming_mode>simple</streaming_mode>
  <endpoint>
    <args/>
  </endpoint>
</scheme>
"""


def main(argv):
    if len(argv) > 1 and argv[1] == "--scheme":
        sys.stdout.write(SCHEME_XML)
        return 0

    logger = get_logger("worker")
    try:
        session_key = _read_session_key(sys.stdin.read())
    except Exception as e:
        logger.error("could not read modular input configuration", error=str(e))
        return 1

    try:
        _run_once(session_key, logger)
    except Exception as e:
        logger.error("worker tick failed", exc_info=True, error=str(e))
        return 1
    return 0


def _run_once(session_key, logger):
    stores = Stores(
        platform_store=PlatformStore(kv_store(session_key, PlatformStore.collection_name)),
        datasource_store=DataSourceStore(kv_store(session_key, DataSourceStore.collection_name)),
        run_store=ValidationRunStore(kv_store(session_key, ValidationRunStore.collection_name)),
        queue_store=ValidationQueueStore(kv_store(session_key, ValidationQueueStore.collection_name)),
        result_store=ValidationResultStore(kv_store(session_key, ValidationResultStore.collection_name)),
        lease_store=WorkerLeaseStore(kv_store(session_key, WorkerLeaseStore.collection_name)),
    )
    settings_store = SettingsStore(kv_store(session_key, SettingsStore.collection_name))

    service = ServiceHandle(session_key)
    credential = security.get_service_credential(service)
    if credential is None:
        logger.info("no service account credential configured yet - skipping tick")
        return

    try:
        service_session_key = login(credential["username"], credential["clear_password"])
    except SplunkRestError as e:
        logger.error("could not authenticate as the dsv_service account", error=str(e))
        return

    parser = SplunkRestSearchParser(service_session_key)
    query_validator = QueryValidator(parser)
    job_client = SearchJobClient(service_session_key)

    earliest = settings_store.get(SEARCH_TIME_RANGE_EARLIEST, default=DEFAULT_EARLIEST_TIME)
    latest = settings_store.get(SEARCH_TIME_RANGE_LATEST, default=DEFAULT_LATEST_TIME)
    max_time = int(settings_store.get(DEFAULT_DISPATCH_TIMEOUT_SEC, default=DEFAULT_MAX_TIME_SEC))

    executor = SearchExecutor(
        query_validator, job_client, earliest_time=earliest,
        latest_time=latest, max_time_sec=max_time,
    )

    run_tick(
        stores, executor, lease_owner=_lease_owner(), lease_ttl_sec=LEASE_TTL_SEC,
        stuck_job_timeout_sec=DEFAULT_STUCK_JOB_TIMEOUT_SEC, logger=logger,
    )


def _lease_owner():
    """
    Stable per search-head-cluster member, not per process invocation -
    this script is re-spawned every tick, but the lease's whole purpose
    is to let a member reliably re-acquire (renew) the same lease
    tick after tick, and only lose it if it actually goes away.
    """
    return socket.gethostname()


def _read_session_key(stdin_text):
    root = ET.fromstring(stdin_text)
    session_key_el = root.find("session_key")
    if session_key_el is None or not (session_key_el.text or "").strip():
        raise RuntimeError("modular input configuration did not include a session_key")
    return session_key_el.text.strip()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
