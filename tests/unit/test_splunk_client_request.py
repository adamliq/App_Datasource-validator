"""
Covers app.splunk_client.request()'s exception handling - specifically
that a status code is recovered from splunk.rest exceptions that carry
one, since splunk.rest.simpleRequest has been observed (live, against a
real Splunk instance) to raise for a 404 even with raiseAllErrors=False,
and KVCollectionClient.get() depends on SplunkRestError.status to treat
"not found" as a normal, non-error outcome.
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from app import splunk_client  # noqa: E402


class FakeSplunkRestException(Exception):
    """Stands in for splunk.ResourceNotFound and friends, which expose
    the real HTTP status via .statusCode, not via any standard Python
    exception attribute."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.statusCode = status_code


class RequestStatusRecoveryTests(unittest.TestCase):
    def setUp(self):
        self._real_splunk_rest = splunk_client.splunk_rest
        self.fake_module = mock.Mock()
        splunk_client.splunk_rest = self.fake_module

    def tearDown(self):
        splunk_client.splunk_rest = self._real_splunk_rest

    def test_recovers_status_code_from_raised_exception(self):
        self.fake_module.simpleRequest.side_effect = FakeSplunkRestException(
            "https://127.0.0.1:8089/.../nonexistent_key?output_mode=json", 404
        )
        with self.assertRaises(splunk_client.SplunkRestError) as ctx:
            splunk_client.request("tok", "/some/path/nonexistent_key")
        self.assertEqual(ctx.exception.status, 404)

    def test_exception_without_status_code_leaves_status_none(self):
        self.fake_module.simpleRequest.side_effect = RuntimeError("connection reset")
        with self.assertRaises(splunk_client.SplunkRestError) as ctx:
            splunk_client.request("tok", "/some/path")
        self.assertIsNone(ctx.exception.status)

    def test_kv_collection_get_returns_none_on_404_raised_as_exception(self):
        self.fake_module.simpleRequest.side_effect = FakeSplunkRestException(
            "https://127.0.0.1:8089/.../missing_key?output_mode=json", 404
        )
        client = splunk_client.KVCollectionClient("tok", "dsv_settings")
        self.assertIsNone(client.get("missing_key"))


if __name__ == "__main__":
    unittest.main()
