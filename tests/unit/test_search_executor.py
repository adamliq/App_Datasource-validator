import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeSearchJobClient, FakeSearchParser  # noqa: E402

from app.query_validator import ParsedSearch, QueryValidator  # noqa: E402
from search_executor import (  # noqa: E402
    STATUS_ERROR,
    STATUS_FAIL,
    STATUS_PASS,
    SearchExecutor,
)


def make_executor(job_client=None):
    parser = FakeSearchParser()
    validator = QueryValidator(parser)
    return SearchExecutor(validator, job_client or FakeSearchJobClient()), parser


class DispatchTests(unittest.TestCase):
    def test_rejected_query_never_reaches_job_client(self):
        job_client = FakeSearchJobClient()
        executor, parser = make_executor(job_client)
        q = "search index=main | outputlookup x.csv"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search", "outputlookup"]))

        result = executor.dispatch(q)
        self.assertFalse(result.allowed)
        self.assertIsNone(result.sid)
        self.assertEqual(job_client.dispatch_calls, [])

    def test_allowed_query_dispatches_with_bounded_time_range(self):
        job_client = FakeSearchJobClient()
        executor, parser = make_executor(job_client)
        q = "search index=main"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search"]))

        result = executor.dispatch(q)
        self.assertTrue(result.allowed)
        self.assertIsNotNone(result.sid)
        self.assertEqual(len(job_client.dispatch_calls), 1)
        call = job_client.dispatch_calls[0]
        self.assertTrue(call["query"].endswith("| head 1"))
        self.assertIsNotNone(call["earliest_time"])
        self.assertIsNotNone(call["latest_time"])
        self.assertIsNotNone(call["max_time_sec"])

    def test_job_client_exception_becomes_disallowed_result(self):
        class BrokenJobClient(FakeSearchJobClient):
            def dispatch(self, *a, **kw):
                raise RuntimeError("connection refused")

        executor, parser = make_executor(BrokenJobClient())
        q = "search index=main"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search"]))
        result = executor.dispatch(q)
        self.assertFalse(result.allowed)
        self.assertIn("dispatch failed", result.reason)


class PollTests(unittest.TestCase):
    def test_still_running_returns_none(self):
        job_client = FakeSearchJobClient()
        executor, parser = make_executor(job_client)
        q = "search index=main"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search"]))
        result = executor.dispatch(q)

        outcome = executor.poll(result.sid)
        self.assertIsNone(outcome)

    def test_done_with_results_is_pass(self):
        job_client = FakeSearchJobClient()
        executor, parser = make_executor(job_client)
        q = "search index=main"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search"]))
        result = executor.dispatch(q)
        job_client.set_job_state(result.sid, is_done=True, result_count=5)

        outcome = executor.poll(result.sid)
        self.assertEqual(outcome.status, STATUS_PASS)
        self.assertEqual(outcome.result_count, 5)

    def test_done_with_zero_results_is_fail(self):
        job_client = FakeSearchJobClient()
        executor, parser = make_executor(job_client)
        q = "search index=main"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search"]))
        result = executor.dispatch(q)
        job_client.set_job_state(result.sid, is_done=True, result_count=0)

        outcome = executor.poll(result.sid)
        self.assertEqual(outcome.status, STATUS_FAIL)

    def test_failed_job_is_error_with_message(self):
        job_client = FakeSearchJobClient()
        executor, parser = make_executor(job_client)
        q = "search index=main"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search"]))
        result = executor.dispatch(q)
        job_client.set_job_state(result.sid, is_done=True, is_failed=True, messages=[{"text": "field not found"}])

        outcome = executor.poll(result.sid)
        self.assertEqual(outcome.status, STATUS_ERROR)
        self.assertIn("field not found", outcome.error_message)

    def test_cancel_delegates_to_job_client(self):
        job_client = FakeSearchJobClient()
        executor, parser = make_executor(job_client)
        q = "search index=main"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search"]))
        result = executor.dispatch(q)
        executor.cancel(result.sid)
        self.assertEqual(job_client.cancelled, [result.sid])


if __name__ == "__main__":
    unittest.main()
