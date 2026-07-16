"""
bin/rest_validation.py - CRUD for platforms and data sources, plus run
lifecycle (create/cancel), the live queue, and result history. Backs 8 of
restmap.conf's 11 endpoints; see HANDOFF.md's run lifecycle description
for how these fit together (UI creates a run here, the worker modular
input - validation_controller.py - is the only thing that ever dispatches
work off that run's queue).
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import security  # noqa: E402
from app.models.base import NotFoundError  # noqa: E402
from app.models.datasource import DataSourceStore  # noqa: E402
from app.models.platform import PlatformStore  # noqa: E402
from app.models.settings import DEFAULT_STALE_THRESHOLD_SEC, SettingsStore  # noqa: E402
from app.models.validation_queue import QUEUE_STATUS_QUEUED, ValidationQueueStore  # noqa: E402
from app.models.validation_result import ValidationResultStore  # noqa: E402
from app.models.validation_run import ValidationRunStore  # noqa: E402
from app.models.worker_lease import WorkerLeaseStore  # noqa: E402
from app.query_validator import QueryValidator  # noqa: E402
from app.rest_base import DsvPersistentHandler  # noqa: E402
from app.splunk_client import SplunkRestSearchParser, get_current_user_capabilities, kv_store  # noqa: E402
from app.status_calculator import compute_datasource_display_status, compute_platform_status  # noqa: E402
from validation_controller import Stores, cancel_run, create_run  # noqa: E402

PLATFORM_UPDATE_FIELDS = {"name", "description", "sort_order", "enabled"}
DATASOURCE_METADATA_FIELDS = {"name", "description", "sort_order", "enabled", "stale_threshold_sec"}


def _stores(session_key):
    return Stores(
        platform_store=PlatformStore(kv_store(session_key, PlatformStore.collection_name)),
        datasource_store=DataSourceStore(kv_store(session_key, DataSourceStore.collection_name)),
        run_store=ValidationRunStore(kv_store(session_key, ValidationRunStore.collection_name)),
        queue_store=ValidationQueueStore(kv_store(session_key, ValidationQueueStore.collection_name)),
        result_store=ValidationResultStore(kv_store(session_key, ValidationResultStore.collection_name)),
        lease_store=WorkerLeaseStore(kv_store(session_key, WorkerLeaseStore.collection_name)),
    )


def _default_stale_threshold(session_key):
    settings_store = SettingsStore(kv_store(session_key, SettingsStore.collection_name))
    value = settings_store.get(DEFAULT_STALE_THRESHOLD_SEC)
    return int(value) if value else None


def _is_admin(session_key):
    return "dsv_admin_config" in get_current_user_capabilities(session_key)


def _validate_query_or_raise(session_key, spl_query, is_admin):
    validator = QueryValidator(SplunkRestSearchParser(session_key))
    result = validator.validate(spl_query, is_admin=is_admin)
    if not result.allowed:
        raise ValueError(f"query rejected: {result.reason}")


def _platform_dict(platform, children):
    status = compute_platform_status(platform, children)
    return {
        "platform_id": platform.platform_id,
        "name": platform.name,
        "description": platform.description,
        "sort_order": platform.sort_order,
        "enabled": platform.enabled,
        "datasource_count": len(children),
        "status": status.status,
        "status_label": status.label,
        "status_icon": status.icon,
        "status_description": status.description,
    }


def _datasource_dict(ds, threshold):
    status = compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
    return {
        "datasource_id": ds.datasource_id,
        "platform_id": ds.platform_id,
        "name": ds.name,
        "description": ds.description,
        "spl_query": ds.spl_query,
        "query_version": ds.query_version,
        "sort_order": ds.sort_order,
        "enabled": ds.enabled,
        "stale_threshold_sec": ds.stale_threshold_sec,
        "last_result_time": ds.last_result_time,
        "last_run_id": ds.last_run_id,
        "status": status.status,
        "status_stale": status.stale,
        "status_label": status.label,
        "status_icon": status.icon,
        "status_description": status.description,
    }


class DsvPlatformsHandler(DsvPersistentHandler):
    def handle_get(self, request):
        stores = _stores(request.session_key)
        threshold = _default_stale_threshold(request.session_key)
        platforms = stores.platform_store.list()
        datasources = stores.datasource_store.list()

        by_platform = defaultdict(list)
        for ds in datasources:
            by_platform[ds.platform_id].append(
                compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
            )

        return {
            "platforms": [_platform_dict(p, by_platform.get(p.platform_id, [])) for p in platforms]
        }

    def handle_post(self, request):
        payload = request.payload or {}
        stores = _stores(request.session_key)
        platform = stores.platform_store.create(
            platform_id=payload.get("platform_id"),
            name=payload.get("name"),
            description=payload.get("description", ""),
            sort_order=payload.get("sort_order", 0),
            enabled=payload.get("enabled", True),
        )
        return _platform_dict(platform, []), 201


class DsvPlatformHandler(DsvPersistentHandler):
    def _platform_id(self, request):
        if not request.path_segments:
            raise ValueError("platform_id is required in the URL path")
        return request.path_segments[0]

    def handle_get(self, request):
        platform_id = self._platform_id(request)
        stores = _stores(request.session_key)
        platform = stores.platform_store.get(platform_id)
        if platform is None:
            raise NotFoundError(f"no such platform: {platform_id}")
        threshold = _default_stale_threshold(request.session_key)
        children = [
            compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
            for ds in stores.datasource_store.list(platform_id=platform_id)
        ]
        return _platform_dict(platform, children)

    def handle_put(self, request):
        return self.handle_post(request)

    def handle_post(self, request):
        platform_id = self._platform_id(request)
        payload = request.payload or {}
        fields = {k: v for k, v in payload.items() if k in PLATFORM_UPDATE_FIELDS}
        stores = _stores(request.session_key)
        platform = stores.platform_store.update(platform_id, **fields)
        threshold = _default_stale_threshold(request.session_key)
        children = [
            compute_datasource_display_status(ds, default_stale_threshold_sec=threshold)
            for ds in stores.datasource_store.list(platform_id=platform_id)
        ]
        return _platform_dict(platform, children)

    def handle_delete(self, request):
        platform_id = self._platform_id(request)
        stores = _stores(request.session_key)
        if stores.platform_store.get(platform_id) is None:
            raise NotFoundError(f"no such platform: {platform_id}")
        for ds in stores.datasource_store.list(platform_id=platform_id):
            stores.datasource_store.delete(ds.datasource_id)
        stores.platform_store.delete(platform_id)
        return {"deleted": platform_id}


class DsvDatasourcesHandler(DsvPersistentHandler):
    def handle_get(self, request):
        stores = _stores(request.session_key)
        threshold = _default_stale_threshold(request.session_key)
        platform_id = request.query.get("platform_id")
        datasources = stores.datasource_store.list(platform_id=platform_id)
        return {"datasources": [_datasource_dict(ds, threshold) for ds in datasources]}

    def handle_post(self, request):
        payload = request.payload or {}
        spl_query = payload.get("spl_query")
        if not spl_query:
            raise ValueError("spl_query is required")
        _validate_query_or_raise(request.session_key, spl_query, _is_admin(request.session_key))

        stores = _stores(request.session_key)
        platform_id = payload.get("platform_id")
        security.validate_identifier(platform_id or "", "platform_id")
        if stores.platform_store.get(platform_id) is None:
            raise NotFoundError(f"no such platform: {platform_id}")

        ds = stores.datasource_store.create(
            datasource_id=payload.get("datasource_id"),
            platform_id=platform_id,
            name=payload.get("name"),
            spl_query=spl_query,
            description=payload.get("description", ""),
            sort_order=payload.get("sort_order", 0),
            enabled=payload.get("enabled", True),
            stale_threshold_sec=payload.get("stale_threshold_sec"),
        )
        threshold = _default_stale_threshold(request.session_key)
        return _datasource_dict(ds, threshold), 201


class DsvDatasourceHandler(DsvPersistentHandler):
    def _datasource_id(self, request):
        if not request.path_segments:
            raise ValueError("datasource_id is required in the URL path")
        return request.path_segments[0]

    def handle_get(self, request):
        datasource_id = self._datasource_id(request)
        stores = _stores(request.session_key)
        ds = stores.datasource_store.get(datasource_id)
        if ds is None:
            raise NotFoundError(f"no such data source: {datasource_id}")
        threshold = _default_stale_threshold(request.session_key)
        return _datasource_dict(ds, threshold)

    def handle_put(self, request):
        return self.handle_post(request)

    def handle_post(self, request):
        datasource_id = self._datasource_id(request)
        payload = request.payload or {}
        stores = _stores(request.session_key)

        if stores.datasource_store.get(datasource_id) is None:
            raise NotFoundError(f"no such data source: {datasource_id}")

        if "spl_query" in payload and payload["spl_query"]:
            _validate_query_or_raise(request.session_key, payload["spl_query"], _is_admin(request.session_key))
            stores.datasource_store.update_query(datasource_id, payload["spl_query"])

        metadata_fields = {k: v for k, v in payload.items() if k in DATASOURCE_METADATA_FIELDS}
        if metadata_fields:
            stores.datasource_store.update_metadata(datasource_id, **metadata_fields)

        ds = stores.datasource_store.get(datasource_id)
        threshold = _default_stale_threshold(request.session_key)
        return _datasource_dict(ds, threshold)

    def handle_delete(self, request):
        datasource_id = self._datasource_id(request)
        stores = _stores(request.session_key)
        if stores.datasource_store.get(datasource_id) is None:
            raise NotFoundError(f"no such data source: {datasource_id}")
        stores.datasource_store.delete(datasource_id)
        return {"deleted": datasource_id}


class DsvRunsHandler(DsvPersistentHandler):
    def handle_get(self, request):
        stores = _stores(request.session_key)
        status = request.query.get("status")
        limit = int(request.query["limit"]) if request.query.get("limit") else None
        runs = stores.run_store.list(status=status, limit=limit)
        return {"runs": [_run_dict(r) for r in runs]}

    def handle_post(self, request):
        payload = request.payload or {}
        stores = _stores(request.session_key)
        datasource_ids = payload.get("datasource_ids")
        run = create_run(
            stores, created_by=request.user or "unknown",
            trigger_type=payload.get("trigger_type", "manual"),
            datasource_ids=set(datasource_ids) if datasource_ids else None,
        )
        return _run_dict(run), 201


class DsvRunHandler(DsvPersistentHandler):
    def _run_id(self, request):
        if not request.path_segments:
            raise ValueError("run_id is required in the URL path")
        return request.path_segments[0]

    def handle_get(self, request):
        run_id = self._run_id(request)
        stores = _stores(request.session_key)
        run = stores.run_store.get(run_id)
        if run is None:
            raise NotFoundError(f"no such run: {run_id}")
        items = stores.queue_store.list_for_run(run_id)
        return {"run": _run_dict(run), "items": [_queue_item_dict(i) for i in items]}

    def handle_post(self, request):
        run_id = self._run_id(request)
        stores = _stores(request.session_key)
        run = cancel_run(stores, run_id)
        return _run_dict(run)

    def handle_delete(self, request):
        return self.handle_post(request)


class DsvQueueHandler(DsvPersistentHandler):
    def handle_get(self, request):
        stores = _stores(request.session_key)
        run_id = request.query.get("run_id")
        if run_id:
            items = stores.queue_store.list_for_run(run_id)
            return {"run_id": run_id, "items": [_queue_item_dict(i) for i in items]}

        running = stores.queue_store.find_any_running()
        return {
            "running": _queue_item_dict(running) if running else None,
            "queued_depth": stores.queue_store.count_global_status(QUEUE_STATUS_QUEUED),
        }


class DsvResultsHandler(DsvPersistentHandler):
    def handle_get(self, request):
        stores = _stores(request.session_key)
        datasource_id = request.query.get("datasource_id")
        run_id = request.query.get("run_id")
        limit = int(request.query["limit"]) if request.query.get("limit") else None

        if datasource_id:
            results = stores.result_store.list_for_datasource(datasource_id, limit=limit)
        elif run_id:
            results = stores.result_store.list_for_run(run_id)
        else:
            raise ValueError("either datasource_id or run_id is required")

        return {"results": [_result_dict(r) for r in results]}


def _run_dict(run):
    return {
        "run_id": run.run_id,
        "created_time": run.created_time,
        "created_by": run.created_by,
        "trigger_type": run.trigger_type,
        "status": run.status,
        "total_items": run.total_items,
        "completed_items": run.completed_items,
        "completed_time": run.completed_time,
    }


def _queue_item_dict(item):
    return {
        "queue_id": item.queue_id,
        "run_id": item.run_id,
        "platform_id": item.platform_id,
        "datasource_id": item.datasource_id,
        "order_index": item.order_index,
        "status": item.status,
        "sid": item.sid,
        "dispatched_time": item.dispatched_time,
        "completed_time": item.completed_time,
    }


def _result_dict(result):
    return {
        "result_id": result.result_id,
        "run_id": result.run_id,
        "datasource_id": result.datasource_id,
        "platform_id": result.platform_id,
        "status": result.status,
        "result_count": result.result_count,
        "executed_time": result.executed_time,
        "duration_ms": result.duration_ms,
        "error_message": result.error_message,
    }
