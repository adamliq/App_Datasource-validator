# Release Notes — Data Source Validator

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
