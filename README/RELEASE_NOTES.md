# Release Notes — Data Source Validator

## 1.0.9 — [DATE]

Investigated the modular-input registration gap further in Docker, with
DEBUG-level Splunk logging enabled. Finding: Splunk's `SpecFiles` scanner,
which is how every *other* modular input in that test environment (all
Splunk-bundled: `checkapp`, `search_artifacts_helper`,
`splunk_secure_gateway`'s inputs, etc.) actually gets its scheme registered,
never once opens this app's `README/inputs.conf.spec` - confirmed by its
complete absence from a full-verbosity log capture that repeatedly scanned
every bundled app's equivalent file. Ruled out, each tested directly: the
worker script's executable bit, `disabled`/`is_configured` state, a live
app reinstall, and a true container first-boot with the app present from
creation. This points at a restriction on third-party/customer-installed
apps specifically in that test image/version, not at anything fixable in
this app's own packaging - still unconfirmed either way against real
Splunk Cloud infrastructure, which is different from the Docker sandbox
used here.

Two real, concrete things came out of the investigation regardless:
- Added `README/inputs.conf.spec`, documenting the `dsv_validation_worker`
  input - standard practice, harmless either way, and it is what let
  AppInspect itself recognize this as a modular input for the first time
  (previously true for `restmap.conf`'s script stanzas only).
- That recognition surfaced a real AppInspect failure
  (`check_inputs_conf_spec_stanzas_has_python_version_property`):
  `python.version` must be declared on the modular input's *scheme-level*
  stanza (`[dsv_validation_worker]`, not just the `://main` instance
  stanza). Added, alongside `python.required`, matching the convention
  already used in `restmap.conf`.

## 1.0.8 — [DATE]

Found and fixed by driving the setup page's actual save flow end-to-end
against a live Splunk Enterprise instance (Docker), past the fixes in
1.0.4-1.0.7:

- `bin/app/splunk_client.py`'s `request()` discarded the HTTP status code
  whenever `splunk.rest.simpleRequest()` raised an exception rather than
  returning a response object - confirmed live that it does exactly this
  for a KV Store GET on a document that doesn't exist yet
  (`splunk.ResourceNotFound`, `.statusCode = 404`), even with
  `raiseAllErrors=False`. Every `SplunkRestError` built from that path got
  `status=None`, so `KVCollectionClient.get()`'s `if e.status == 404:
  return None` could never match - turning the routine "this setting
  isn't set yet" case (true on every first-time setup save) into an
  unhandled 500. Now recovered from the exception's `.statusCode`.

## 1.0.7 — [DATE]

Two fixes confirmed against a real Splunk Enterprise instance (Docker), found
while investigating why the `dsv_validation_worker` modular input never
registers:

- `default/savedsearches.conf` had an invalid `is_scheduled = 1` key in both
  stanzas. `is_scheduled` is a read-only field Splunk reports back over the
  REST API (derived from `enableSched`), not a real settable conf key -
  Splunk's own startup config check flagged it as invalid. Removed; the
  already-present `enableSched = 1` is what actually enables scheduling.
- `bin/dsv_validation_worker.py` was packaged non-executable (644). Modular
  inputs are invoked by Splunk executing the script directly (via its
  shebang), unlike REST persist handlers which Splunk invokes through an
  explicit `python3 <script>` call - a non-executable modular input script
  can be silently skipped during discovery. Now packaged 755, matching
  AppInspect's own file-permission guidance.

Still unresolved: in local Docker testing (a recent Splunk Enterprise build),
the modular input scheme does not get auto-discovered even with both fixes
applied, and reproduces identically with a trivial throwaway test app
unrelated to this one - strong evidence this specific gap is an
environment/version characteristic of that test setup, not a defect in this
app's code, but it means the live Splunk Cloud "modular input never
registers" report is not yet confirmed fixed end-to-end.

## 1.0.6 — [DATE]

- Fixed the setup page's Save button failing with "Error starting:
  More than one class implements PersistentServerConnectionApplication".
  Splunk's persist-connection loader scans a REST handler script's own
  top-level namespace for every class satisfying `issubclass(_,
  PersistentServerConnectionApplication)`, and refuses to start if it
  finds more than one. `from app.rest_base import DsvPersistentHandler`
  made the shared base class itself a top-level name in every handler
  script, alongside each script's own concrete handler(s) - `bin/rest_
  config.py`, `bin/rest_query.py`, `bin/rest_health.py`, and `bin/rest_
  validation.py` (8 handlers) now import `rest_base` as a module and
  reference `rest_base.DsvPersistentHandler`, keeping the base class out
  of that scan.

## 1.0.5 — [DATE]

- Granted this app's 4 custom capabilities (`dsv_admin_config`,
  `dsv_manage_datasources`, `dsv_run_validation`, `dsv_view_results`)
  directly to Splunk's built-in `admin` and Splunk Cloud `sc_admin`
  roles in `authorize.conf`, in addition to the app's own
  `dsv_admin`/`dsv_editor`/`dsv_viewer` roles. Previously an admin had
  to separately discover and assign `role_dsv_admin` (or hand-check
  these capabilities) before the setup page was usable at all - now
  any built-in admin gets them automatically. Only adds capability
  keys, never touches `importRoles`/`grantableRoles`, so it merges
  with rather than replaces what those roles already grant.

## 1.0.4 — [DATE]

- Fixed the real cause of the setup page's "insufficient permission to
  access this resource" (403) on Save: `mvc.createService()` was called
  with no arguments, leaving its REST-call namespace unpinned. A live
  report showed POST going to
  `/servicesNS/<the just-typed service-account username>/...` while the
  tester was authenticated as a different (admin) account - Splunk's
  REST framework checks namespace-crossing rights on that owner segment
  independently of the endpoint's own capability gate, and correctly
  rejected it. This app's config (KV Store, `storage/passwords`) is
  app-level, not user-private, and the server side already targets the
  `nobody` namespace for it; the client now does too
  (`mvc.createService({ owner: 'nobody' })`).
- Bumped `[install] build` again, since this touches
  `appserver/static/app.js`.

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
