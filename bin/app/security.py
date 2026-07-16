"""
Security helpers: encrypted credential storage, capability checks, and
input sanitization.

Nothing in this module talks to the network directly - every function
that needs Splunk platform access (storage/passwords) takes a duck-typed
`service` object as a parameter (matching the shape of
splunklib.client.Service.storage_passwords, i.e. an object exposing
.list(), .create(password, username=..., realm=...) and .delete(name)).
That keeps this module unit-testable with a fake in the tests directory
and keeps the *real* client construction (with TLS, real credentials,
real host) entirely in Phase 3's REST/worker entry points.
"""
import re

REALM = "datasource_validator"
SERVICE_ACCOUNT_USERNAME_KEY = "dsv_service_account"

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,127}$")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

SECRET_FIELD_NAMES = {"password", "secret", "token", "credential", "authtoken"}


class SecurityError(Exception):
    """Base class for security-layer failures."""


class CapabilityError(SecurityError):
    """Raised when the acting session lacks a required capability."""

    def __init__(self, capability):
        super().__init__(f"missing required capability: {capability}")
        self.capability = capability


class ValidationError(SecurityError):
    """Raised when caller-supplied input fails validation."""


class CredentialError(SecurityError):
    """Raised when storage/passwords access fails or is inconsistent."""


# ---------------------------------------------------------------------------
# Capability checks (defense in depth - restmap.conf already gates each
# REST endpoint per HTTP method; this is a second check inside the handler
# so a future endpoint or code path can't accidentally skip enforcement).
# ---------------------------------------------------------------------------

def require_capability(session, capability):
    """
    session: a dict-like object describing the acting user's session, with
    a "capabilities" key holding an iterable of capability names.
    Raises CapabilityError if the capability is absent.
    """
    capabilities = set((session or {}).get("capabilities") or ())
    if capability not in capabilities:
        raise CapabilityError(capability)


def has_capability(session, capability):
    capabilities = set((session or {}).get("capabilities") or ())
    return capability in capabilities


# ---------------------------------------------------------------------------
# Credential storage (storage/passwords) - the only place a service-account
# password is ever handled. Never logged, never returned to the browser,
# never written anywhere but the platform's own encrypted store.
# ---------------------------------------------------------------------------

def get_service_credential(service):
    """
    Return {"username": ..., "clear_password": ...} for the configured
    service account, or None if setup hasn't stored one yet.

    `service` must expose a `storage_passwords` attribute that is iterable
    of objects with .username, .clear_password, .realm attributes (this is
    exactly the shape of splunklib.client.StoragePasswords).
    """
    passwords = getattr(service, "storage_passwords", None)
    if passwords is None:
        raise CredentialError("service object has no storage_passwords collection")

    matches = [p for p in passwords if getattr(p, "realm", None) == REALM]
    if not matches:
        return None
    if len(matches) > 1:
        raise CredentialError(
            f"expected at most one credential in realm {REALM!r}, found "
            f"{len(matches)} - setup page should have replaced, not "
            f"duplicated, the prior entry"
        )
    entry = matches[0]
    return {
        "username": entry.username,
        "clear_password": entry.clear_password,
    }


def set_service_credential(service, username, password):
    """
    Store (or replace) the service-account credential used for every
    validation search. Any prior credential in this realm is deleted
    first so exactly one always exists - storage/passwords entries are
    identified by (realm, username) and are not mutable in place.
    """
    validate_identifier(username, "username", max_length=254, allow_at=True)
    if not password:
        raise ValidationError("password must not be empty")

    passwords = getattr(service, "storage_passwords", None)
    if passwords is None:
        raise CredentialError("service object has no storage_passwords collection")

    for existing in list(passwords):
        if getattr(existing, "realm", None) == REALM:
            passwords.delete(existing.name)

    passwords.create(password, username=username, realm=REALM)


def delete_service_credential(service):
    passwords = getattr(service, "storage_passwords", None)
    if passwords is None:
        raise CredentialError("service object has no storage_passwords collection")
    deleted = False
    for existing in list(passwords):
        if getattr(existing, "realm", None) == REALM:
            passwords.delete(existing.name)
            deleted = True
    return deleted


# ---------------------------------------------------------------------------
# Input validation / sanitization
# ---------------------------------------------------------------------------

def validate_identifier(value, field_name, max_length=128, allow_at=False):
    """
    Validate a KV Store key / platform id / data source id / username.
    Fail closed: reject anything that isn't a plain, bounded token rather
    than trying to enumerate every dangerous character.
    """
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} must be a non-empty string")
    if len(value) > max_length:
        raise ValidationError(
            f"{field_name} exceeds maximum length {max_length}"
        )
    if _CONTROL_CHARS_RE.search(value):
        raise ValidationError(f"{field_name} contains control characters")

    pattern = _IDENTIFIER_RE
    if allow_at:
        # service-account usernames may be email addresses
        pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.@]{0,253}$")
    if not pattern.match(value):
        raise ValidationError(f"{field_name} contains disallowed characters")
    return value


def validate_display_text(value, field_name, max_length=1000):
    """
    Looser check for free-text fields (names, descriptions) that still
    must not contain control characters or exceed a sane length.
    """
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string")
    if len(value) > max_length:
        raise ValidationError(f"{field_name} exceeds maximum length {max_length}")
    if _CONTROL_CHARS_RE.search(value):
        raise ValidationError(f"{field_name} contains control characters")
    return value


def redact(obj):
    """
    Return a shallow copy of a dict with any key that looks secret
    replaced by a fixed marker, for safe inclusion in log lines.
    """
    if not isinstance(obj, dict):
        return obj
    redacted = {}
    for key, value in obj.items():
        if isinstance(key, str) and key.lower() in SECRET_FIELD_NAMES:
            redacted[key] = "***REDACTED***"
        else:
            redacted[key] = value
    return redacted
