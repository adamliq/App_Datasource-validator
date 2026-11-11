"""
Real Splunk integration - the only module in this app that talks to the
platform over HTTP. Deliberately depends on nothing but the `splunk`
package Splunk itself puts on sys.path for every script it runs (splunk.rest)
plus the standard library: no vendored third-party SDK, nothing for
AppInspect's dependency scan to worry about, and nothing that needs
network access to install.

Every class here implements one of the small duck-typed protocols the
rest of bin/app/* was written against in Phase 2 (models/base.py's Store
protocol, security.py's storage_passwords protocol,
query_validator.SearchParser), so the business logic already has full
unit test coverage against fakes and this module only needs to be
correct about the wire format. /services/search/parser's request shape
was confirmed live against a real Splunk instance: it requires POST
with `q` in the form body (GET returns 405). SplunkRestSearchParser
still fails closed (marks the parse ambiguous) if a response ever
doesn't look like what's expected, rather than guessing.
"""
import json
import time

try:
    import splunk.rest as splunk_rest
except ImportError:  # pragma: no cover - only importable inside splunkd
    splunk_rest = None

APP_NAME = "datasource_validator"
DEFAULT_OWNER = "nobody"


class SplunkRestError(Exception):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


def _require_rest_module():
    if splunk_rest is None:
        raise SplunkRestError(
            "splunk.rest is not importable - this module only runs inside "
            "a Splunk-managed Python process (REST handler or modular input)"
        )


def request(session_key, path, method="GET", getargs=None, postargs=None, raise_on_error=True):
    """
    Thin wrapper over splunk.rest.simpleRequest that always asks for JSON
    and raises SplunkRestError on a non-2xx response instead of forcing
    every caller to check status codes by hand.
    """
    _require_rest_module()
    getargs = dict(getargs or {})
    getargs.setdefault("output_mode", "json")
    try:
        server_response, server_content = splunk_rest.simpleRequest(
            path,
            sessionKey=session_key,
            method=method,
            getargs=getargs,
            postargs=postargs,
            raiseAllErrors=False,
        )
    except Exception as e:
        # splunk.rest.simpleRequest raises (e.g. splunk.ResourceNotFound for
        # a 404) for some conditions even with raiseAllErrors=False - most
        # notably a GET for a KV Store document that doesn't exist, which
        # every caller of this module treats as a normal, expected outcome
        # (see KVCollectionClient.get()'s 404 -> None). Confirmed live:
        # splunk's own REST exceptions carry the real status on
        # .statusCode; without recovering it here every such exception
        # loses its status (None) and 404-tolerant callers can never match
        # it, turning a routine "not found" into an unhandled 500.
        raise SplunkRestError(f"request to {path} failed: {e}", status=getattr(e, "statusCode", None))

    status = getattr(server_response, "status", None)
    if raise_on_error and (status is None or status >= 300):
        raise SplunkRestError(
            f"{method} {path} returned status {status}: {server_content!r}", status=status, body=server_content,
        )

    if not server_content:
        return {}, status
    try:
        return json.loads(server_content), status
    except (ValueError, TypeError):
        if raise_on_error:
            raise SplunkRestError(f"{method} {path} did not return valid JSON")
        return {}, status


def login(username, password):
    """
    Exchange the dsv_service account's stored credential for its own
    session key, scoped to exactly that role's privileges. This is the
    auth-layer boundary from HANDOFF.md: every validation search and
    every ad-hoc "test query" runs under this session, never under the
    (typically far more privileged) session Splunk hands the modular
    input or a REST handler for its own KV Store access.
    """
    _require_rest_module()
    server_response, server_content = splunk_rest.simpleRequest(
        "/services/auth/login",
        method="POST",
        postargs={"username": username, "password": password, "output_mode": "json"},
        raiseAllErrors=False,
    )
    status = getattr(server_response, "status", None)
    if status is None or status >= 300:
        raise SplunkRestError(f"login for {username!r} failed with status {status}", status=status)
    try:
        body = json.loads(server_content)
    except (ValueError, TypeError):
        raise SplunkRestError("login response was not valid JSON")
    session_key = body.get("sessionKey")
    if not session_key:
        raise SplunkRestError("login response did not include a sessionKey")
    return session_key


def get_current_user_capabilities(session_key):
    """
    Used only as a defense-in-depth admin check for the leading-command
    admin allowlist (query_validator.py) - restmap.conf's capability.*
    gates are the primary enforcement for every endpoint's basic access.
    Fails closed: any error getting the caller's capabilities is treated
    as "not an admin" rather than raised, since callers use this to
    decide whether to relax a safety check, never to tighten one.
    """
    try:
        body, _ = request(session_key, "/services/authentication/current-context", method="GET")
    except SplunkRestError:
        return []
    entries = body.get("entry", []) if isinstance(body, dict) else []
    if not entries:
        return []
    return entries[0].get("content", {}).get("capabilities", []) or []


def set_app_configured(session_key, app=APP_NAME):
    """Flips default/app.conf's [install] is_configured to true once setup completes."""
    request(
        session_key, f"/servicesNS/nobody/{app}/properties/app/install",
        method="POST", postargs={"is_configured": "true"},
    )


def enable_modular_input(session_key, stanza_name="main", input_type="dsv_validation_worker", app=APP_NAME):
    """Flips inputs.conf's disabled = 1 to enabled, once a service credential exists."""
    request(
        session_key,
        f"/servicesNS/nobody/{app}/data/inputs/{input_type}/{stanza_name}/enable",
        method="POST",
    )


# ---------------------------------------------------------------------------
# KV Store adapter -> matches app.models.base.Store's expected `collection`
# protocol exactly: insert / get / query / update / delete / delete_query.
# ---------------------------------------------------------------------------

class KVCollectionClient:
    def __init__(self, session_key, collection_name, app=APP_NAME, owner=DEFAULT_OWNER):
        self._session_key = session_key
        self._collection_name = collection_name
        self._base_path = f"/servicesNS/{owner}/{app}/storage/collections/data/{collection_name}"

    def insert(self, doc):
        # KV Store's insert endpoint expects the JSON document as the raw
        # POST body, not form-encoded postargs, so this goes through
        # simpleRequest's `jsonargs` rather than the `request()` helper.
        _require_rest_module()
        server_response, server_content = splunk_rest.simpleRequest(
            self._base_path,
            sessionKey=self._session_key,
            method="POST",
            jsonargs=json.dumps(doc),
            getargs={"output_mode": "json"},
            raiseAllErrors=False,
        )
        status = getattr(server_response, "status", None)
        if status is None or status >= 300:
            raise SplunkRestError(f"insert into {self._collection_name} failed: {status} {server_content!r}", status=status)
        return json.loads(server_content) if server_content else doc

    def get(self, key):
        try:
            body, _ = request(self._session_key, f"{self._base_path}/{key}", method="GET")
        except SplunkRestError as e:
            if e.status == 404:
                return None
            raise
        return body or None

    def query(self, query=None, sort=None, limit=None, skip=None):
        getargs = {}
        if query:
            getargs["query"] = json.dumps(query)
        if sort:
            getargs["sort"] = sort
        if limit:
            getargs["limit"] = limit
        if skip:
            getargs["skip"] = skip
        body, _ = request(self._session_key, self._base_path, method="GET", getargs=getargs)
        return body if isinstance(body, list) else []

    def update(self, key, doc):
        _require_rest_module()
        server_response, server_content = splunk_rest.simpleRequest(
            f"{self._base_path}/{key}",
            sessionKey=self._session_key,
            method="POST",
            jsonargs=json.dumps(doc),
            getargs={"output_mode": "json"},
            raiseAllErrors=False,
        )
        status = getattr(server_response, "status", None)
        if status is None or status >= 300:
            raise SplunkRestError(f"update of {self._collection_name}/{key} failed: {status}", status=status)
        return doc

    def delete(self, key):
        request(self._session_key, f"{self._base_path}/{key}", method="DELETE", raise_on_error=False)

    def delete_query(self, query):
        request(
            self._session_key, self._base_path, method="DELETE",
            getargs={"query": json.dumps(query)}, raise_on_error=False,
        )


def kv_store(session_key, collection_name, app=APP_NAME, owner=DEFAULT_OWNER):
    return KVCollectionClient(session_key, collection_name, app=app, owner=owner)


# ---------------------------------------------------------------------------
# storage/passwords adapter -> matches security.py's expectation of a
# `service.storage_passwords` iterable with .create()/.delete().
# ---------------------------------------------------------------------------

class StoragePasswordEntry:
    def __init__(self, name, username, clear_password, realm):
        self.name = name
        self.username = username
        self.clear_password = clear_password
        self.realm = realm


class StoragePasswordsClient:
    def __init__(self, session_key, app=APP_NAME, owner=DEFAULT_OWNER):
        self._session_key = session_key
        self._app = app
        self._owner = owner
        self._base_path = f"/servicesNS/{owner}/{app}/storage/passwords"

    def __iter__(self):
        body, _ = request(self._session_key, self._base_path, method="GET", getargs={"count": 0})
        entries = body.get("entry", []) if isinstance(body, dict) else []
        result = []
        for entry in entries:
            content = entry.get("content", {})
            result.append(StoragePasswordEntry(
                name=entry.get("name"),
                username=content.get("username"),
                clear_password=content.get("clear_password"),
                realm=content.get("realm"),
            ))
        return iter(result)

    def create(self, password, username=None, realm=None):
        postargs = {"password": password, "name": username}
        if realm:
            postargs["realm"] = realm
        body, _ = request(
            self._session_key, self._base_path, method="POST",
            postargs=postargs, getargs={"output_mode": "json"},
        )
        entries = body.get("entry", []) if isinstance(body, dict) else []
        if entries:
            content = entries[0].get("content", {})
            return StoragePasswordEntry(
                name=entries[0].get("name"), username=content.get("username"),
                clear_password=content.get("clear_password"), realm=content.get("realm"),
            )
        return StoragePasswordEntry(name=username, username=username, clear_password=password, realm=realm)

    def delete(self, name):
        request(self._session_key, f"{self._base_path}/{name}", method="DELETE", raise_on_error=False)


class ServiceHandle:
    """
    The `service` object security.py's credential functions expect:
    anything with a `.storage_passwords` attribute. Kept separate from
    KVCollectionClient/SearchJobClient because storage/passwords access is
    always scoped to the app's own realm, never per-collection.
    """
    def __init__(self, session_key, app=APP_NAME, owner=DEFAULT_OWNER):
        self.storage_passwords = StoragePasswordsClient(session_key, app=app, owner=owner)


# ---------------------------------------------------------------------------
# /services/search/parser adapter -> implements query_validator.SearchParser.
# ---------------------------------------------------------------------------

from app.query_validator import (  # noqa: E402
    ParsedSearch,
    ParserUnavailableError,
    SearchParser,
    SearchRejectedError,
)


class SplunkRestSearchParser(SearchParser):
    """
    Uses Splunk's own search parser to normalize and macro-expand a query
    (handling quoting, comments, and subsearches correctly, which a
    hand-rolled regex over untrusted raw SPL cannot do reliably), then
    tokenizes the *normalized* string into top-level pipeline stages with
    a small bracket/quote-aware scanner. If the response doesn't contain
    the field this depends on, the parse is reported ambiguous rather
    than guessed at - fail closed per HANDOFF.md's query safety rules.
    """

    def __init__(self, session_key, app=APP_NAME, owner=DEFAULT_OWNER):
        self._session_key = session_key
        self._path = f"/servicesNS/{owner}/{app}/search/parser"

    def parse(self, spl_query):
        try:
            # Confirmed live against a real Splunk instance: this endpoint
            # rejects GET with 405 "The method is not allowed" - it must be
            # POST, with q in the form body rather than the query string.
            body, _ = request(self._session_key, self._path, method="POST", postargs={"q": spl_query})
        except SplunkRestError as e:
            # A 400 is the parser's verdict on the search itself (confirmed
            # live: a typo or a forbidden command comes back this way), not
            # an outage - pass Splunk's own explanation on to the user.
            if e.status == 400:
                raise SearchRejectedError(_response_messages(e.body) or str(e))
            raise ParserUnavailableError(str(e))

        normalized = _extract_normalized_search(body)
        if normalized is None:
            return ParsedSearch(
                leading_command="", main_pipeline_commands=[], all_commands=[],
                ambiguous=True,
                ambiguous_reason="search/parser response did not contain a recognizable normalized search",
            )

        try:
            main_commands, all_commands = _tokenize_pipeline(normalized)
        except _TokenizeError as e:
            return ParsedSearch(
                leading_command="", main_pipeline_commands=[], all_commands=[],
                ambiguous=True, ambiguous_reason=str(e),
            )

        leading = main_commands[0] if main_commands else ""
        return ParsedSearch(
            leading_command=leading,
            main_pipeline_commands=main_commands,
            all_commands=all_commands,
        )


def _response_messages(body):
    """Joins the text of a splunkd {"messages": [...]} error body, or ''."""
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", errors="replace")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return ""
    if not isinstance(body, dict):
        return ""
    texts = [m.get("text") for m in body.get("messages") or [] if isinstance(m, dict) and m.get("text")]
    return "; ".join(texts)


def _extract_normalized_search(body):
    if not isinstance(body, dict):
        return None
    entries = body.get("entry")
    if isinstance(entries, list) and entries:
        content = entries[0].get("content", {})
        normalized = content.get("search") or content.get("eai:search")
        if isinstance(normalized, str) and normalized.strip():
            return normalized
    # Confirmed live: this Splunk version's search/parser response has no
    # "entry" wrapper - it's a flat dict whose "eventsSearch" field is the
    # normalized pipeline using the original (non-macro-expanded) command
    # names, e.g. "search index=_internal | head 1". "normalizedSearch" is
    # NOT usable here - it's remapped to internal command names (e.g.
    # "prehead") and has extra "| fields ..." stages appended.
    events_search = body.get("eventsSearch")
    if isinstance(events_search, str) and events_search.strip():
        return events_search
    # Some Splunk versions return the parsed search at the top level.
    top_level = body.get("search")
    if isinstance(top_level, str) and top_level.strip():
        return top_level
    return None


class _TokenizeError(Exception):
    pass


def _tokenize_pipeline(normalized_search):
    """
    Splits a normalized SPL string into (main-pipeline commands,
    all commands including those inside subsearches), tracking quote and
    bracket depth so `|` inside a quoted string or a subsearch does not
    split the outer pipeline. This runs only over text Splunk's own
    parser has already normalized, not over arbitrary raw user input.
    """
    text = normalized_search.strip()
    if text.startswith("|"):
        text = text[1:]

    segments, all_subsearch_commands = _split_top_level(text)
    main_commands = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        first_token = seg.split()[0] if seg.split() else ""
        main_commands.append(first_token.lower())

    if not main_commands:
        main_commands = ["search"]  # a bare filter expression is an implicit search

    all_commands = list(main_commands) + all_subsearch_commands
    return main_commands, all_commands


def _split_top_level(text):
    """
    Returns (top_level_pipe_segments, commands_found_inside_subsearches).
    Recurses into [...] subsearches to collect their commands for the
    denylist scan, without treating their content as part of the main
    pipeline (subsearches are separate search jobs).
    """
    segments = []
    subsearch_commands = []
    current = []
    depth = 0
    quote = None
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            current.append(ch)
            if ch == "\\" and i + 1 < n:
                current.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == "[":
            depth += 1
            sub_start = i + 1
            sub_depth = 1
            j = sub_start
            sub_quote = None
            while j < n and sub_depth > 0:
                cj = text[j]
                if sub_quote:
                    if cj == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if cj == sub_quote:
                        sub_quote = None
                elif cj in ("'", '"'):
                    sub_quote = cj
                elif cj == "[":
                    sub_depth += 1
                elif cj == "]":
                    sub_depth -= 1
                j += 1
            if sub_depth != 0:
                raise _TokenizeError("unbalanced '[' in normalized search")
            inner = text[sub_start:j - 1]
            inner_segments, inner_sub_commands = _split_top_level(inner)
            for seg in inner_segments:
                seg = seg.strip()
                if seg:
                    subsearch_commands.append((seg.split()[0] if seg.split() else "").lower())
            subsearch_commands.extend(inner_sub_commands)
            current.append(text[i:j])
            depth -= 1
            i = j
            continue
        elif ch == "]":
            raise _TokenizeError("unbalanced ']' in normalized search")
        elif ch == "|" and depth == 0:
            segments.append("".join(current))
            current = []
            i += 1
            continue
        else:
            current.append(ch)
        i += 1

    if quote:
        raise _TokenizeError("unterminated quoted string in normalized search")
    segments.append("".join(current))
    return segments, subsearch_commands


# ---------------------------------------------------------------------------
# Search job dispatch/poll -> used by bin/search_executor.py.
# ---------------------------------------------------------------------------

def _as_search_command(executable_query):
    """
    /services/search/jobs's `search` arg must be a full search command
    (starting with "search" or "|"), not a bare filter expression - but
    the stored query is normally already a full "search ..." pipeline
    (query_validator.py passes it through as-is). Confirmed live: always
    prepending "search " regardless produced "search search index=main
    ..." for an already-prefixed query, which Splunk parses as searching
    for the literal term "search" - every validation failed with zero
    results even against indexes/data the service account could read.
    """
    stripped = executable_query.strip()
    if stripped.startswith("|") or stripped.lower().startswith("search"):
        return executable_query
    return f"search {executable_query}"


class SearchJobClient:
    def __init__(self, session_key, app=APP_NAME, owner=DEFAULT_OWNER):
        self._session_key = session_key
        self._jobs_path = f"/servicesNS/{owner}/{app}/search/jobs"

    def dispatch(self, executable_query, earliest_time, latest_time, max_time_sec):
        postargs = {
            "search": _as_search_command(executable_query),
            "earliest_time": earliest_time,
            "latest_time": latest_time,
            "max_time": max_time_sec,
            "exec_mode": "normal",
        }
        body, _ = request(self._session_key, self._jobs_path, method="POST", postargs=postargs)
        sid = body.get("sid") if isinstance(body, dict) else None
        if not sid:
            raise SplunkRestError(f"dispatch did not return a sid: {body!r}")
        return sid

    def poll(self, sid):
        body, _ = request(self._session_key, f"{self._jobs_path}/{sid}", method="GET")
        content = _job_content(body)
        return {
            "is_done": _as_bool(content.get("isDone")),
            "is_failed": _as_bool(content.get("isFailed")),
            "dispatch_state": content.get("dispatchState"),
            "result_count": int(content.get("resultCount") or 0),
            "messages": content.get("messages") or [],
        }

    def cancel(self, sid):
        request(
            self._session_key, f"{self._jobs_path}/{sid}/control", method="POST",
            postargs={"action": "cancel"}, raise_on_error=False,
        )


def _job_content(body):
    if isinstance(body, dict):
        entries = body.get("entry")
        if isinstance(entries, list) and entries:
            return entries[0].get("content", {})
        if "content" in body:
            return body["content"]
    return {}


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def sleep(seconds):  # thin wrapper so tests can monkeypatch if ever needed
    time.sleep(seconds)
