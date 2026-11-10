"""
Covers app.splunk_client.SearchJobClient.dispatch()'s construction of the
`search` POST arg for /services/search/jobs.

Confirmed live against a real Splunk instance: unconditionally prepending
"search " to an executable_query that already started with "search "
produced "search search index=main ...", which Splunk parses as searching
for the literal term "search" - every validation failed with zero results
even against indexes/data the service account could read.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from app import splunk_client  # noqa: E402


class SearchJobClientDispatchTests(unittest.TestCase):
    def _dispatch_and_capture_search_arg(self, executable_query):
        client = splunk_client.SearchJobClient(session_key="fake-key")
        with mock.patch.object(
            splunk_client, "request", return_value=({"sid": "123"}, 200)
        ) as mock_request:
            client.dispatch(executable_query, "-24h@h", "now", 120)
        return mock_request.call_args.kwargs["postargs"]["search"]

    def test_leaves_a_search_prefixed_query_unchanged(self):
        search_arg = self._dispatch_and_capture_search_arg(
            "search index=main sourcetype=dsv_demo | head 1"
        )
        self.assertEqual(search_arg, "search index=main sourcetype=dsv_demo | head 1")

    def test_prepends_search_to_a_bare_filter_expression(self):
        search_arg = self._dispatch_and_capture_search_arg("index=main | head 1")
        self.assertEqual(search_arg, "search index=main | head 1")

    def test_leaves_a_leading_pipe_query_unchanged(self):
        search_arg = self._dispatch_and_capture_search_arg("| makeresults")
        self.assertEqual(search_arg, "| makeresults")

    def test_case_insensitive_search_prefix_detection(self):
        search_arg = self._dispatch_and_capture_search_arg("SEARCH index=main")
        self.assertEqual(search_arg, "SEARCH index=main")


if __name__ == "__main__":
    unittest.main()
