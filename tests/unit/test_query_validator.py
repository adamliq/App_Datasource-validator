import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeSearchParser  # noqa: E402

from app.query_validator import (  # noqa: E402
    ParsedSearch,
    ParserUnavailableError,
    QueryValidator,
    compute_query_hash,
)


def make_validator(parser=None):
    return QueryValidator(parser or FakeSearchParser())


class ComputeQueryHashTests(unittest.TestCase):
    def test_same_query_same_hash(self):
        self.assertEqual(compute_query_hash("search index=main"), compute_query_hash("search index=main"))

    def test_whitespace_change_changes_hash(self):
        self.assertNotEqual(compute_query_hash("search index=main"), compute_query_hash("search index=main "))


class LeadingCommandTests(unittest.TestCase):
    def test_plain_search_is_allowed(self):
        parser = FakeSearchParser()
        q = "search index=main sourcetype=foo"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search"]))
        result = make_validator(parser).validate(q)
        self.assertTrue(result.allowed)
        self.assertEqual(result.leading_command, "search")

    def test_tstats_is_allowed(self):
        parser = FakeSearchParser()
        q = "| tstats count where index=main"
        parser.stub(q, ParsedSearch(leading_command="tstats", main_pipeline_commands=["tstats"]))
        result = make_validator(parser).validate(q)
        self.assertTrue(result.allowed)

    def test_all_six_readonly_leading_commands_allowed(self):
        for cmd in ("search", "tstats", "mstats", "from", "datamodel", "metadata", "inputlookup"):
            with self.subTest(cmd=cmd):
                parser = FakeSearchParser()
                q = f"| {cmd} something"
                parser.stub(q, ParsedSearch(leading_command=cmd, main_pipeline_commands=[cmd]))
                result = make_validator(parser).validate(q)
                self.assertTrue(result.allowed, msg=result.reason)

    def test_non_readonly_leading_command_rejected_for_non_admin(self):
        parser = FakeSearchParser()
        q = "| pivot Datamodel Object"
        parser.stub(q, ParsedSearch(leading_command="pivot", main_pipeline_commands=["pivot"]))
        result = make_validator(parser).validate(q, is_admin=False)
        self.assertFalse(result.allowed)
        self.assertIn("not read-only", result.reason)

    def test_non_readonly_leading_command_allowed_for_admin_on_allowlist(self):
        parser = FakeSearchParser()
        q = "| pivot Datamodel Object"
        parser.stub(q, ParsedSearch(leading_command="pivot", main_pipeline_commands=["pivot"]))
        validator = QueryValidator(parser, admin_leading_commands={"pivot"})
        result = validator.validate(q, is_admin=True)
        self.assertTrue(result.allowed)

    def test_admin_allowlist_command_still_rejected_for_non_admin(self):
        parser = FakeSearchParser()
        q = "| pivot Datamodel Object"
        parser.stub(q, ParsedSearch(leading_command="pivot", main_pipeline_commands=["pivot"]))
        validator = QueryValidator(parser, admin_leading_commands={"pivot"})
        result = validator.validate(q, is_admin=False)
        self.assertFalse(result.allowed)


class DenylistTests(unittest.TestCase):
    def test_denylisted_command_in_main_pipeline_rejected(self):
        parser = FakeSearchParser()
        q = "search index=main | outputlookup mylookup.csv"
        parser.stub(q, ParsedSearch(
            leading_command="search",
            main_pipeline_commands=["search", "outputlookup"],
        ))
        result = make_validator(parser).validate(q)
        self.assertFalse(result.allowed)
        self.assertIn("outputlookup", result.reason)

    def test_denylisted_command_only_in_subsearch_still_rejected(self):
        parser = FakeSearchParser()
        q = "search index=main [search index=other | delete]"
        parser.stub(q, ParsedSearch(
            leading_command="search",
            main_pipeline_commands=["search"],
            all_commands=["search", "search", "delete"],
        ))
        result = make_validator(parser).validate(q)
        self.assertFalse(result.allowed)
        self.assertIn("delete", result.reason)

    def test_every_default_denylisted_command_is_rejected(self):
        from app.query_validator import DEFAULT_DENYLIST
        for cmd in DEFAULT_DENYLIST:
            with self.subTest(cmd=cmd):
                parser = FakeSearchParser()
                q = f"search index=main | {cmd}"
                parser.stub(q, ParsedSearch(
                    leading_command="search",
                    main_pipeline_commands=["search", cmd],
                ))
                result = make_validator(parser).validate(q)
                self.assertFalse(result.allowed)

    def test_denylist_cannot_be_bypassed_by_admin_flag(self):
        parser = FakeSearchParser()
        q = "search index=main | collect index=summary"
        parser.stub(q, ParsedSearch(
            leading_command="search",
            main_pipeline_commands=["search", "collect"],
        ))
        result = make_validator(parser).validate(q, is_admin=True)
        self.assertFalse(result.allowed)


class ParserFailureTests(unittest.TestCase):
    def test_parser_unavailable_rejects(self):
        parser = FakeSearchParser()
        q = "search index=main"
        parser.stub(q, ParserUnavailableError("connection refused"))
        result = make_validator(parser).validate(q)
        self.assertFalse(result.allowed)
        self.assertIn("unavailable", result.reason)

    def test_unexpected_parser_exception_rejects(self):
        parser = FakeSearchParser()
        q = "search index=main"
        parser.stub(q, RuntimeError("boom"))
        result = make_validator(parser).validate(q)
        self.assertFalse(result.allowed)

    def test_ambiguous_parse_rejects(self):
        parser = FakeSearchParser()
        q = "search `unresolvable_macro`"
        parser.stub(q, ParsedSearch(
            leading_command="search",
            main_pipeline_commands=["search"],
            ambiguous=True,
            ambiguous_reason="macro could not be expanded",
        ))
        result = make_validator(parser).validate(q)
        self.assertFalse(result.allowed)
        self.assertIn("ambiguous", result.reason)

    def test_empty_query_rejects_without_calling_parser(self):
        result = make_validator().validate("   ")
        self.assertFalse(result.allowed)


class HeadAppendTests(unittest.TestCase):
    def test_pure_streaming_search_gets_head_appended(self):
        parser = FakeSearchParser()
        q = "search index=main sourcetype=foo | eval x=1 | where x=1 | table x"
        parser.stub(q, ParsedSearch(
            leading_command="search",
            main_pipeline_commands=["search", "eval", "where", "table"],
        ))
        result = make_validator(parser).validate(q)
        self.assertTrue(result.allowed)
        self.assertTrue(result.head_appended)
        self.assertEqual(result.executable_query, q + " | head 1")

    def test_stats_disqualifies_head_append(self):
        parser = FakeSearchParser()
        q = "search index=main | stats count by host"
        parser.stub(q, ParsedSearch(
            leading_command="search",
            main_pipeline_commands=["search", "stats"],
        ))
        result = make_validator(parser).validate(q)
        self.assertTrue(result.allowed)
        self.assertFalse(result.head_appended)
        self.assertEqual(result.executable_query, q)

    def test_tstats_leading_command_never_gets_head_append(self):
        parser = FakeSearchParser()
        q = "| tstats count where index=main by host"
        parser.stub(q, ParsedSearch(leading_command="tstats", main_pipeline_commands=["tstats"]))
        result = make_validator(parser).validate(q)
        self.assertTrue(result.allowed)
        self.assertFalse(result.head_appended)
        self.assertEqual(result.executable_query, q)

    def test_head_command_itself_disqualifies_append(self):
        parser = FakeSearchParser()
        q = "search index=main | head 50"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search", "head"]))
        result = make_validator(parser).validate(q)
        self.assertTrue(result.allowed)
        self.assertFalse(result.head_appended)

    def test_head_append_never_alters_query_when_not_eligible(self):
        parser = FakeSearchParser()
        q = "search index=main | sort -_time"
        parser.stub(q, ParsedSearch(leading_command="search", main_pipeline_commands=["search", "sort"]))
        result = make_validator(parser).validate(q)
        self.assertEqual(result.executable_query, q)


if __name__ == "__main__":
    unittest.main()
