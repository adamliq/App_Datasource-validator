"""
Tests for the pure-logic pieces of app.splunk_client's search/parser
adapter: the normalized-search extractor and the bracket/quote-aware
pipeline tokenizer. These need no real `splunk` package and no network -
SplunkRestSearchParser.parse() itself is exercised with app.splunk_client
.request monkeypatched, since that's the one seam that talks to splunkd.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from app.splunk_client import (  # noqa: E402
    SplunkRestError,
    SplunkRestSearchParser,
    _extract_normalized_search,
    _split_top_level,
    _tokenize_pipeline,
)
from app.query_validator import ParserUnavailableError  # noqa: E402


class ExtractNormalizedSearchTests(unittest.TestCase):
    def test_extracts_from_entry_content_search(self):
        body = {"entry": [{"content": {"search": "search index=main"}}]}
        self.assertEqual(_extract_normalized_search(body), "search index=main")

    def test_extracts_from_eai_search_fallback(self):
        body = {"entry": [{"content": {"eai:search": "search index=main"}}]}
        self.assertEqual(_extract_normalized_search(body), "search index=main")

    def test_extracts_from_top_level_search(self):
        body = {"search": "search index=main"}
        self.assertEqual(_extract_normalized_search(body), "search index=main")

    def test_returns_none_when_nothing_recognizable(self):
        self.assertIsNone(_extract_normalized_search({"unexpected": "shape"}))
        self.assertIsNone(_extract_normalized_search("not even a dict"))
        self.assertIsNone(_extract_normalized_search({"entry": []}))


class TokenizePipelineTests(unittest.TestCase):
    def test_simple_pipeline(self):
        main, all_cmds = _tokenize_pipeline("search index=main | eval x=1 | where x=1")
        self.assertEqual(main, ["search", "eval", "where"])
        self.assertEqual(all_cmds, main)

    def test_leading_pipe_generating_command(self):
        main, _ = _tokenize_pipeline("| tstats count where index=main")
        self.assertEqual(main, ["tstats"])

    def test_pipe_inside_quotes_does_not_split(self):
        main, _ = _tokenize_pipeline('search index=main foo="a|b" | eval x=1')
        self.assertEqual(main, ["search", "eval"])

    def test_subsearch_commands_collected_separately(self):
        main, all_cmds = _tokenize_pipeline("search index=main [search index=other | delete]")
        self.assertEqual(main, ["search"])
        self.assertIn("delete", all_cmds)
        self.assertNotIn("delete", main)

    def test_nested_subsearches(self):
        main, all_cmds = _tokenize_pipeline(
            "search index=main [search index=b [search index=c | outputlookup x.csv]]"
        )
        self.assertEqual(main, ["search"])
        self.assertIn("outputlookup", all_cmds)

    def test_unbalanced_bracket_raises(self):
        with self.assertRaises(Exception):
            _split_top_level("search index=main [search index=b")

    def test_unterminated_quote_raises(self):
        with self.assertRaises(Exception):
            _split_top_level('search index=main foo="unterminated')


class SplunkRestSearchParserTests(unittest.TestCase):
    def test_parse_success(self):
        parser = SplunkRestSearchParser(session_key="fake-key")
        response_body = {"entry": [{"content": {"search": "search index=main | stats count"}}]}
        with patch("app.splunk_client.request", return_value=(response_body, 200)):
            result = parser.parse("index=main | stats count")
        self.assertFalse(result.ambiguous)
        self.assertEqual(result.leading_command, "search")
        self.assertIn("stats", result.main_pipeline_commands)

    def test_parse_marks_ambiguous_on_unrecognized_shape(self):
        parser = SplunkRestSearchParser(session_key="fake-key")
        with patch("app.splunk_client.request", return_value=({"nothing": "useful"}, 200)):
            result = parser.parse("index=main")
        self.assertTrue(result.ambiguous)

    def test_parse_raises_parser_unavailable_on_rest_error(self):
        parser = SplunkRestSearchParser(session_key="fake-key")
        with patch("app.splunk_client.request", side_effect=SplunkRestError("connection refused")):
            with self.assertRaises(ParserUnavailableError):
                parser.parse("index=main")


if __name__ == "__main__":
    unittest.main()
