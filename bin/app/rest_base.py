"""
Shared plumbing for the bin/rest_*.py persistent REST handlers
(splunk.persistconn.application.PersistentServerConnectionApplication
subclasses registered in restmap.conf with scripttype = persist).

Splunk hands `handle(in_string)` a JSON string describing the request
(method, path, query args, JSON body, and a `session` dict with the
caller's authtoken/user) and expects back a dict with a `payload` and a
`status`. This module centralizes that parsing/response-building and the
mapping from this app's own exception types (security.py,
models/base.py) to HTTP status codes, so every bin/rest_*.py handler
only has to write its actual business logic.

restmap.conf already enforces the per-method capability gate for every
endpoint before Splunk even invokes the handler - the exception mapping
here is defense in depth, not the primary enforcement point.
"""
import json

try:
    from splunk.persistconn.application import PersistentServerConnectionApplication
except ImportError:  # pragma: no cover - only importable inside splunkd
    class PersistentServerConnectionApplication:
        def __init__(self, *args, **kwargs):
            pass

from app import security
from app.models.base import NotFoundError


class RestRequest:
    def __init__(self, raw):
        self.method = (raw.get("method") or "GET").upper()
        self.path_segments = [p for p in (raw.get("path") or "").split("/") if p]
        self.query = _pairs_to_dict(raw.get("query") or [])
        self.session = raw.get("session") or {}
        self.session_key = self.session.get("authtoken")
        self.user = self.session.get("user")
        self.payload = _parse_payload(raw.get("payload"))


def _pairs_to_dict(pairs):
    result = {}
    for pair in pairs:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            result[pair[0]] = pair[1]
    return result


def _parse_payload(raw_payload):
    if raw_payload in (None, ""):
        return {}
    if isinstance(raw_payload, (dict, list)):
        return raw_payload
    try:
        return json.loads(raw_payload)
    except (ValueError, TypeError):
        return {}


def json_response(payload_obj, status=200):
    return {"payload": json.dumps(payload_obj, default=str), "status": status}


def error_response(message, status=400):
    return json_response({"error": message}, status=status)


class DsvPersistentHandler(PersistentServerConnectionApplication):
    """
    Subclasses implement handle_get/handle_post/handle_put/handle_delete,
    each taking (request: RestRequest) and returning a plain JSON-able
    object (not the {"payload": ..., "status": ...} envelope - this base
    class wraps that, and turns exceptions into the right status code).
    """

    def __init__(self, command_line=None, command_arg=None):
        PersistentServerConnectionApplication.__init__(self)

    def handle(self, in_string):
        try:
            raw = json.loads(in_string) if in_string else {}
        except (ValueError, TypeError):
            return error_response("could not parse request", status=400)

        request = RestRequest(raw)
        method_handler = {
            "GET": getattr(self, "handle_get", None),
            "POST": getattr(self, "handle_post", None),
            "PUT": getattr(self, "handle_put", None),
            "DELETE": getattr(self, "handle_delete", None),
        }.get(request.method)

        if method_handler is None:
            return error_response(f"method {request.method} not supported here", status=405)

        try:
            result = method_handler(request)
        except security.ValidationError as e:
            return error_response(str(e), status=400)
        except security.CapabilityError as e:
            return error_response(str(e), status=403)
        except security.CredentialError as e:
            return error_response(str(e), status=400)
        except NotFoundError as e:
            return error_response(str(e), status=404)
        except ValueError as e:
            return error_response(str(e), status=400)
        except Exception as e:  # fail closed: never leak a stack trace to the client
            return error_response(f"internal error: {e}", status=500)

        if isinstance(result, tuple):
            body, status = result
            return json_response(body, status=status)
        return json_response(result, status=200)
