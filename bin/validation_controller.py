"""
Core orchestration logic for the single dsv_validation_worker dispatcher:
run creation/cancellation, and the per-tick "poll the one in-flight job,
else dispatch the next queued item" loop described in HANDOFF.md's
architecture. Every dependency (model stores, a SearchExecutor) is passed
in already constructed, so this module has zero import-time dependency on
the real Splunk platform and is fully unit-testable with Phase 2's
in-memory fakes - see tests/unit/test_validation_controller.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.models.base import NotFoundError, parse_iso, utcnow_iso  # noqa: E402
from app.models.datasource import (  # noqa: E402
    STATUS_CANCELLED,
    STATUS_ERROR,
    STATUS_QUEUED,
    STATUS_RUNNING,
)
from app.models.validation_queue import (  # noqa: E402
    QUEUE_STATUS_CANCELLED,
    QUEUE_STATUS_DONE,
    QUEUE_STATUS_ERROR,
    QUEUE_STATUS_QUEUED,
    QUEUE_STATUS_RUNNING,
)
from app.models.validation_run import RUN_STATUS_RUNNING  # noqa: E402
from search_executor import DEFAULT_STUCK_JOB_TIMEOUT_SEC  # noqa: E402


class Stores:
    """Groups the 6 model stores a tick/run needs (settings is handled separately)."""

    def __init__(self, platform_store, datasource_store, run_store, queue_store, result_store, lease_store):
        self.platform_store = platform_store
        self.datasource_store = datasource_store
        self.run_store = run_store
        self.queue_store = queue_store
        self.result_store = result_store
        self.lease_store = lease_store


def create_run(stores: Stores, created_by, trigger_type, datasource_ids=None):
    """
    Build a new validation run and its ordered queue, and mark each
    selected data source QUEUED. Ordering: run creation order (this run's
    items always land after any already-queued work, since the worker
    processes runs oldest-first) -> platform.sort_order ->
    datasource.sort_order -> name, exactly as locked in HANDOFF.md.

    datasource_ids: optional explicit subset to validate; None means
    "every enabled data source under an enabled platform".
    """
    platforms = {p.platform_id: p for p in stores.platform_store.list()}
    all_datasources = stores.datasource_store.list()

    selected = []
    for ds in all_datasources:
        if datasource_ids is not None and ds.datasource_id not in datasource_ids:
            continue
        if not ds.enabled:
            continue
        platform = platforms.get(ds.platform_id)
        if platform is None or not platform.enabled:
            continue
        selected.append(ds)

    if not selected:
        raise ValueError("no enabled data sources under an enabled platform to validate")

    def sort_key(ds):
        platform = platforms.get(ds.platform_id)
        return (platform.sort_order if platform else 0, ds.sort_order, ds.name)

    selected.sort(key=sort_key)

    run = stores.run_store.create(created_by=created_by, trigger_type=trigger_type, total_items=len(selected))

    queue_items = [
        {
            "platform_id": ds.platform_id,
            "datasource_id": ds.datasource_id,
            "order_index": idx,
            "query_hash": ds.query_hash,
            "query_version": ds.query_version,
        }
        for idx, ds in enumerate(selected)
    ]
    stores.queue_store.create_batch(run.run_id, queue_items)

    for ds in selected:
        stores.datasource_store.update_status(ds.datasource_id, STATUS_QUEUED, last_run_id=run.run_id)

    return run


def cancel_run(stores: Stores, run_id):
    run = stores.run_store.get(run_id)
    if run is None:
        raise NotFoundError(f"no such run: {run_id}")

    stores.queue_store.cancel_queued_for_run(run_id)
    for item in stores.queue_store.list_for_run(run_id, status=QUEUE_STATUS_CANCELLED):
        stores.datasource_store.update_status(item.datasource_id, STATUS_CANCELLED)

    if run.status == RUN_STATUS_RUNNING:
        # A still-RUNNING queue item is left to finish (or hit its own
        # stuck-job timeout) on a later tick, which then completes the
        # run; only flip the run itself to CANCELLED if nothing is
        # in flight right now.
        still_running = stores.queue_store.list_for_run(run_id, status=QUEUE_STATUS_RUNNING)
        if not still_running:
            stores.run_store.cancel(run_id)

    return run


def run_tick(
    stores: Stores, search_executor, lease_owner, lease_ttl_sec=30,
    stuck_job_timeout_sec=DEFAULT_STUCK_JOB_TIMEOUT_SEC, is_admin=False, logger=None,
):
    """
    One iteration of the single dispatcher. Renews the KV Store lease
    first and does nothing else if another live worker holds it - this
    is what guarantees exactly one active worker across
    search-head-cluster members. Then either polls the one in-flight job
    to completion, or dispatches the next queued item; never both in the
    same tick, since a search job in flight already satisfies "at most
    one search job in flight at a time".
    """
    lease = stores.lease_store.try_acquire(lease_owner, lease_ttl_sec)
    if lease is None:
        if logger:
            logger.info("lease held by another worker, skipping tick")
        return

    running_item = stores.queue_store.find_any_running()
    if running_item is not None:
        _handle_running_item(stores, search_executor, running_item, stuck_job_timeout_sec, logger)
        return

    _dispatch_next(stores, search_executor, is_admin, logger)


def _dispatch_next(stores, search_executor, is_admin, logger):
    next_item = _find_next_queued(stores)
    if next_item is None:
        return

    datasource = stores.datasource_store.get(next_item.datasource_id)
    if datasource is None or not datasource.enabled:
        # Deleted or disabled after being queued - drop it rather than
        # dispatching a search for something no longer in scope.
        stores.queue_store.mark_done(next_item.queue_id, status=QUEUE_STATUS_CANCELLED)
        _advance_run_progress(stores, next_item.run_id)
        return

    result = search_executor.dispatch(datasource.spl_query, is_admin=is_admin)
    if not result.allowed:
        stores.queue_store.mark_done(next_item.queue_id, status=QUEUE_STATUS_ERROR)
        stores.result_store.create(
            run_id=next_item.run_id, queue_id=next_item.queue_id,
            datasource_id=datasource.datasource_id, platform_id=datasource.platform_id,
            status=STATUS_ERROR, result_count=0, query_hash=datasource.query_hash,
            query_version=datasource.query_version, error_message=result.reason,
        )
        stores.datasource_store.update_status(
            datasource.datasource_id, STATUS_ERROR,
            last_result_time=utcnow_iso(), last_run_id=next_item.run_id,
        )
        _advance_run_progress(stores, next_item.run_id)
        if logger:
            logger.warning(
                "query rejected at dispatch, recorded as ERROR",
                datasource_id=datasource.datasource_id, reason=result.reason,
            )
        return

    stores.queue_store.mark_running(next_item.queue_id, sid=result.sid)
    stores.datasource_store.update_status(datasource.datasource_id, STATUS_RUNNING)
    if logger:
        logger.info(
            "dispatched search", datasource_id=datasource.datasource_id,
            sid=result.sid, run_id=next_item.run_id,
        )


def _handle_running_item(stores, search_executor, item, stuck_job_timeout_sec, logger):
    if item.dispatched_time:
        elapsed = (parse_iso(utcnow_iso()) - parse_iso(item.dispatched_time)).total_seconds()
        if elapsed > stuck_job_timeout_sec:
            search_executor.cancel(item.sid)
            _finalize(
                stores, item, STATUS_ERROR, 0,
                f"search exceeded the {stuck_job_timeout_sec}s stuck-job timeout and was cancelled",
                logger,
            )
            return

    outcome = search_executor.poll(item.sid)
    if outcome is None:
        return  # still running - nothing to do this tick

    _finalize(stores, item, outcome.status, outcome.result_count, outcome.error_message, logger)


def _finalize(stores, item, status, result_count, error_message, logger):
    queue_status = QUEUE_STATUS_ERROR if status == STATUS_ERROR else QUEUE_STATUS_DONE
    stores.queue_store.mark_done(item.queue_id, status=queue_status)

    now = utcnow_iso()
    stores.result_store.create(
        run_id=item.run_id, queue_id=item.queue_id, datasource_id=item.datasource_id,
        platform_id=item.platform_id, status=status, result_count=result_count,
        query_hash=item.query_hash, query_version=item.query_version,
        error_message=error_message, executed_time=now,
    )
    stores.datasource_store.update_status(
        item.datasource_id, status, last_result_time=now, last_run_id=item.run_id,
    )
    _advance_run_progress(stores, item.run_id)
    if logger:
        logger.info(
            "finalized result", datasource_id=item.datasource_id,
            status=status, result_count=result_count,
        )


def _advance_run_progress(stores, run_id):
    items = stores.queue_store.list_for_run(run_id)
    completed = sum(
        1 for i in items
        if i.status in (QUEUE_STATUS_DONE, QUEUE_STATUS_ERROR, QUEUE_STATUS_CANCELLED)
    )
    stores.run_store.set_progress(run_id, completed)
    if items and completed >= len(items):
        run = stores.run_store.get(run_id)
        if run and run.status == RUN_STATUS_RUNNING:
            stores.run_store.mark_completed(run_id)


def _find_next_queued(stores):
    """
    Global FIFO across all still-active runs: oldest run first (by
    created_time), then order_index within that run.
    """
    active_runs = [r for r in stores.run_store.list() if r.status == RUN_STATUS_RUNNING]
    active_runs.sort(key=lambda r: r.created_time)
    for run in active_runs:
        queued = stores.queue_store.list_for_run(run.run_id, status=QUEUE_STATUS_QUEUED)
        if queued:
            return queued[0]
    return None
