# Data Source Validator

A Splunk app that validates whether each user-defined **data source** currently
returns **at least one event**, with data sources grouped under parent
**platforms**. Targets Splunk Cloud Platform and Splunk Enterprise 9.3+.

## What it does

- You define platforms (e.g. "Firewall", "EDR") and, under each, one or more
  data sources with an SPL search that should return events.
- A single sequential validation worker runs each data source's search (via a
  dedicated, least-privilege service account) on a schedule or on demand, and
  records PASS / FAIL / ERROR / STALE per data source.
- Platform status rolls up from its enabled children so you can see, at a
  glance, which integrations have gone quiet.

## Requirements

- Splunk Enterprise 9.3+ or Splunk Cloud Platform, search head only.
- Python 3.9 (platform-provided Splunk runtime).
- A service account provisioned in the `dsv_service` role, configured on the
  app's setup page after installation.

## Documentation

See the `README/` directory for installation, configuration, user, security,
privacy, support, and troubleshooting guides.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
