"""
Shared helpers for the platform/data-source/run/queue/results REST
handlers (bin/rest_platforms.py, rest_datasources.py, rest_runs.py,
rest_queue.py, rest_results.py).

These handlers used to live together in one bin/rest_validation.py, but
Splunk's persist-connection loader requires exactly one class satisfying
issubclass(_, PersistentServerConnectionApplication) per *script file* -
confirmed live: 8 concrete handlers in one file (even with the shared
base class itself kept out of the file's top-level namespace, per
rest_base.py's own fix) still failed to start with "More than one class
implements PersistentServerConnectionApplication". Splitting into one
script per handler, with the actual logic shared from here (a module
under bin/app/, never itself a REST entry point restmap.conf points at),
fixes that while keeping the logic itself in one place.

rest_platforms.py, rest_datasources.py and rest_runs.py each handle both
their collection path and the single-item path (e.g. /platforms and
/platforms/<id>) in one class, branching on request.path_segments,
rather than splitting those further into a second "item" handler behind
a second, trailing-slash restmap match - see restmap.conf's header
comment for why that second stanza doesn't actually work.
"""
from app.models.base import NotFoundError  # noqa: F401 - re-exported for handler modules
from app.models.datasource import DataSourceStore
from app.models.platform import PlatformStore
from app.models.settings import DEFAULT_STALE_THRESHOLD_SEC, SettingsStore
from app.models.validation_queue import ValidationQueueStore
from app.models.validation_result import ValidationResultStore
from app.models.validation_run import ValidationRunStore
from app.models.worker_lease import WorkerLeaseStore
from app.query_validator import QueryValidator
from app.splunk_client import SplunkRestSearchParser, get_current_user_capabilities, kv_store
from app.status_calculator import compute_datasource_display_status, compute_platform_status
from validation_controller import Stores

PLATFORM_UPDATE_FIELDS = {"name", "description", "sort_order", "enabled"}
DATASOURCE_METADATA_FIELDS = {"name", "description", "sort_order", "enabled", "stale_threshold_sec"}


_TRUE_STRINGS = {"true", "1", "yes", "on"}
_FALSE_STRINGS = {"false", "0", "no", "off"}


def _to_bool(value, field_name):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    raise ValueError(f"{field_name} must be true or false")


def _to_int(value, field_name, minimum):
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a whole number")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a whole number")
    if number < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")
    return number


def clean_metadata(payload, allowed_fields):
    """
    Picks the allowed metadata fields out of a request body and converts
    them to their real types. A form-encoded body (what the app's own UI
    and curl -d send) delivers every value as text, so without this
    "enabled=false" was stored as a truthy string. An empty
    stale_threshold_sec means "use the app default" and becomes None.
    """
    fields = {}
    for key in allowed_fields:
        if key not in payload:
            continue
        value = payload[key]
        if key == "enabled":
            value = _to_bool(value, key)
        elif key == "sort_order":
            value = _to_int(value, key, minimum=0)
        elif key == "stale_threshold_sec":
            value = None if value in (None, "") else _to_int(value, key, minimum=1)
        elif key == "name":
            if not isinstance(value, str) or not value.strip():
                raise ValueError("name must not be empty")
            value = value.strip()
        fields[key] = value
    return fields


def stores(session_key):
    return Stores(
        platform_store=PlatformStore(kv_store(session_key, PlatformStore.collection_name)),
        datasource_store=DataSourceStore(kv_store(session_key, DataSourceStore.collection_name)),
        run_store=ValidationRunStore(kv_store(session_key, ValidationRunStore.collection_name)),
        queue_store=ValidationQueueStore(kv_store(session_key, ValidationQueueStore.collection_name)),
        result_store=ValidationResultStore(kv_store(session_key, ValidationResultStore.collection_name)),
        lease_store=WorkerLeaseStore(kv_store(session_key, WorkerLeaseStore.collection_name)),
    )


def default_stale_threshold(session_key):
    settings_store = SettingsStore(kv_store(session_key, SettingsStore.collection_name))
    value = settings_store.get(DEFAULT_STALE_THRESHOLD_SEC)
    return int(value) if value else None


def is_admin(session_key):
    return "dsv_admin_config" in get_current_user_capabilities(session_key)


def validate_query_or_raise(session_key, spl_query, admin):
    validator = QueryValidator(SplunkRestSearchParser(session_key))
    result = validator.validate(spl_query, is_admin=admin)
    if not result.allowed:
        raise ValueError(f"query rejected: {result.reason}")


def platform_dict(platform, children):
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


def datasource_dict(ds, threshold):
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


def run_dict(run):
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


def queue_item_dict(item):
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


def result_dict(result):
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
