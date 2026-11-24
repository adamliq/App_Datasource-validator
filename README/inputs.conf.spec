[dsv_validation_worker://<name>]
* Single sequential dispatcher for data source validation searches (see
  HANDOFF.md's architecture). Managed entirely by the app's setup page -
  do not add a second stanza, and do not run more than one instance.
interval = <integer>
* Seconds between runs of the worker. Splunk starts the script this often and
  each run does at most one unit of work.
* A stanza in a spec file must list at least one parameter or Splunk does not
  register the input at all, so do not remove this line.
* Default: 5
