"""
bin/rest_config.py - the app's setup page backend (restmap.conf
[script:dsv_config], capability dsv_admin_config on both GET and POST).

GET returns whether setup has completed and the app's non-secret
settings (never the stored password). POST is the only place the
dsv_service credential is ever written: it's verified with a real login
attempt before being persisted, so a typo never silently becomes the
stored credential, and once stored, is_configured flips true and the
validation worker input is enabled.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import security  # noqa: E402
from app.models.settings import (  # noqa: E402
    DEFAULT_DISPATCH_TIMEOUT_SEC,
    DEFAULT_STALE_THRESHOLD_SEC,
    SEARCH_TIME_RANGE_EARLIEST,
    SEARCH_TIME_RANGE_LATEST,
    SettingsStore,
)
from app.rest_base import DsvPersistentHandler  # noqa: E402
from app.splunk_client import (  # noqa: E402
    ServiceHandle,
    SplunkRestError,
    enable_modular_input,
    kv_store,
    login,
    set_app_configured,
)

SETTINGS_FIELDS = {
    DEFAULT_STALE_THRESHOLD_SEC: int,
    DEFAULT_DISPATCH_TIMEOUT_SEC: int,
    SEARCH_TIME_RANGE_EARLIEST: str,
    SEARCH_TIME_RANGE_LATEST: str,
}


class DsvConfigHandler(DsvPersistentHandler):
    def handle_get(self, request):
        service = ServiceHandle(request.session_key)
        credential = security.get_service_credential(service)
        settings_store = SettingsStore(kv_store(request.session_key, SettingsStore.collection_name))

        return {
            "configured": credential is not None,
            "service_username": credential["username"] if credential else None,
            "settings": settings_store.get_all(),
        }

    def handle_post(self, request):
        payload = request.payload or {}
        username = payload.get("username")
        password = payload.get("password")
        if not username or not password:
            raise ValueError("username and password are both required")
        security.validate_identifier(username, "username", allow_at=True)

        try:
            login(username, password)
        except SplunkRestError as e:
            raise ValueError(f"could not authenticate as {username!r} - credential was not saved: {e}")

        service = ServiceHandle(request.session_key)
        security.set_service_credential(service, username, password)

        settings_store = SettingsStore(kv_store(request.session_key, SettingsStore.collection_name))
        for field, caster in SETTINGS_FIELDS.items():
            if field in payload and payload[field] not in (None, ""):
                settings_store.set(field, str(caster(payload[field])), updated_by=request.user)

        set_app_configured(request.session_key)
        enable_modular_input(request.session_key)

        return {"configured": True, "service_username": username}
