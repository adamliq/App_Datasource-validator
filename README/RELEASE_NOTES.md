# Release Notes — Data Source Validator

## 1.0.3 — [DATE]

- Speculative fix (not yet confirmed against a live instance) for the
  setup page's Save button being blocked with a bare "Forbidden" on
  POST while GET succeeded: attaches Splunk Web's CSRF token
  (`X-Splunk-Form-Key`, read from the `splunkweb_csrf_token_<port>`
  cookie) to every AJAX request, since `splunkjs`'s Service object has
  no way to attach a custom header through `post()`/`del()`. Additive
  only - does not weaken CSRF protection.
- Bumped `[install] build` again, since this touches
  `appserver/static/app.js` and Splunk Web's static-asset cache is
  keyed off that number, not the semantic version.

## 1.0.2 — [DATE]

- Bumped `[install] build` (Splunk Web's static-asset cache-busting
  number) so the 1.0.1 setup-page fix actually takes effect in
  browsers/Splunk Web instances that had cached the pre-fix
  `appserver/static/app.js`. No functional changes beyond 1.0.1 - see
  below.

## 1.0.1 — [DATE]

Bug fixes, found via testing against real Splunk infrastructure.

- Fixed the setup page's Save button always failing with an unreadable
  error. Root cause: Splunk's persistent-connection REST protocol
  delivers a form-encoded POST body via a separate `form` field, distinct
  from `payload` (raw JSON bodies only); the REST handler base only read
  `payload`, so every form-encoded POST/PUT - not just setup - landed as
  an empty body server-side.
- Fixed a Splunk Cloud vetting failure (`check_that_app_passes_slim_validation_for_cloud`)
  caused by a stale version table in Splunk's own packaging-validation
  tooling rejecting an accurate Splunk Enterprise 9.3+ compatibility
  declaration. The manifest no longer declares a machine-readable minimum
  version; the real requirement (Splunk Enterprise 9.3+) stays documented
  in README.md.
- Hardened client-side error handling so setup/REST failures show the
  actual server message instead of a generic error.

## 1.0.0 — [DATE]

Initial release.

### Overview
Validates that each user-defined data source — grouped under a parent
platform (e.g. "Firewall", "EDR") — currently returns at least one event.
A single sequential validation worker runs each data source's search on a
schedule or on demand, via a dedicated least-privilege service account,
and records PASS / FAIL / ERROR / STALE per data source. Platform status
rolls up from its enabled children.

### Requirements
- Splunk Enterprise 9.3+ or Splunk Cloud Platform, search head only.
- Python 3.9 (platform-provided Splunk runtime).
- A service account provisioned in the `dsv_service` role, configured on
  the app's setup page after installation.

### Highlights
- Platform / data source hierarchy with roll-up status (green / amber /
  red / running / grey).
- Dedicated, least-privilege service-account search identity for all
  validation searches (batch and ad-hoc "test query"), configured via the
  app's setup page; credentials stored only in Splunk's encrypted
  `storage/passwords`.
- Single-dispatcher modular input worker with KV Store-backed run/queue
  state, so a browser refresh or disconnect never starts duplicate
  searches or loses progress.
- Fail-closed SPL query safety checks before any user-defined search is
  run (parser-based command validation, read-only command allowlist).
- Staleness detection: a prior PASS older than a configurable threshold is
  flagged instead of shown as a false-positive green.

### Known limitations
[List anything not yet supported in this release, e.g. specific dashboard
views still pending, multi-search-head-cluster edge cases not yet
verified in production, etc.]

---
_Format: newest release first. Each entry should note new features, fixes,
and any breaking or upgrade-relevant changes._
