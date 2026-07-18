# Privacy Policy — Data Source Validator

**Status: DRAFT — not yet reviewed or approved for publication.** This is a
skeleton derived from the app's actual architecture (see `HANDOFF.md`) to
satisfy Splunkbase packaging requirements. [YOUR ORG] must review, complete
the bracketed sections, and approve before this app is submitted or
distributed.

## What this app does

Data Source Validator runs customer-defined SPL searches on a schedule (or
on demand) to confirm each configured data source is still returning
events, and stores only the outcome of each check — not the underlying
event data.

## Data this app collects and stores

All data below is stored in this app's own KV Store collections, scoped to
the Splunk instance it is installed on. Nothing is transmitted outside that
Splunk instance or to [YOUR ORG].

- **Configuration you enter**: platform and data source names, descriptions,
  and the SPL search text you define for each data source.
- **Validation outcomes**: pass/fail/error status, the **result count**
  returned by a search (not the events themselves), execution duration,
  timestamps, and error messages surfaced by Splunk when a search fails.
- **Operational state**: run/queue records used to sequence validation
  jobs, and a worker heartbeat/lease used to coordinate across
  search-head-cluster members.
- **Application logs**: operational events (e.g. dispatch, completion,
  errors) written to this app's log file and ingested via Splunk's
  standard `_internal` log monitoring, for troubleshooting.

## Data this app does NOT collect

- **Raw search results or event content.** Validation searches are
  evaluated for result count only (see the query-safety design in
  `HANDOFF.md`); this app does not persist the events a search returns.
- **Personal data about end users of this app**, beyond the Splunk
  username recorded by Splunk's own audit trail for actions taken through
  Splunk Web (outside this app's control).
- **Telemetry sent to [YOUR ORG] or any third party.** This app makes no
  outbound network calls; all searches run against the local Splunk
  instance via the platform's own search API.

## Credentials

The service account credential used to run validation searches is stored
using Splunk's built-in encrypted credential store (`storage/passwords`)
via the app's setup page. It is never written to KV Store, app
configuration files, or logs.

## Data retention

[Describe retention: how long validation run/result history is kept before
being purged by the housekeeping saved search in
`default/savedsearches.conf`, and whether/how a customer can configure or
shorten that retention period.]

## Your responsibilities as the installing organization

Because all data stays within your own Splunk deployment, you are the
controller of any data this app processes (including the content of the
SPL searches you configure, which may reference your own indexes/data).
[YOUR ORG] provides this app as software; [YOUR ORG] does not have access
to your Splunk instance or its data.

## Contact

Questions about this policy: [SUPPORT EMAIL]

## Changes to this policy

[Describe how customers will be notified of material changes, e.g. via
`RELEASE_NOTES.md` and/or the Splunkbase listing.]

---
_Last updated: [DATE]. Version: [VERSION]._
