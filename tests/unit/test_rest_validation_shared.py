"""
Covers app.rest_validation_shared.clean_metadata(): a form-encoded REST
body delivers every value as text, so these fields must be converted to
their real types before they reach the model layer.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from app.security import ValidationError  # noqa: E402
from app.rest_validation_shared import (  # noqa: E402
    DATASOURCE_METADATA_FIELDS,
    PLATFORM_UPDATE_FIELDS,
    clean_metadata,
    parse_id_list,
)


class CleanMetadataTests(unittest.TestCase):
    def test_form_strings_become_real_types(self):
        fields = clean_metadata(
            {"name": "  Firewall  ", "enabled": "false", "sort_order": "3", "stale_threshold_sec": "900"},
            DATASOURCE_METADATA_FIELDS,
        )
        self.assertEqual(fields, {"name": "Firewall", "enabled": False, "sort_order": 3, "stale_threshold_sec": 900})

    def test_json_types_pass_through(self):
        fields = clean_metadata({"enabled": True, "sort_order": 0}, PLATFORM_UPDATE_FIELDS)
        self.assertEqual(fields, {"enabled": True, "sort_order": 0})

    def test_blank_stale_threshold_means_use_default(self):
        self.assertEqual(
            clean_metadata({"stale_threshold_sec": ""}, DATASOURCE_METADATA_FIELDS),
            {"stale_threshold_sec": None},
        )

    def test_only_allowed_fields_are_kept(self):
        fields = clean_metadata(
            {"name": "EDR", "platform_id": "other", "current_status": "PASS", "stale_threshold_sec": "60"},
            PLATFORM_UPDATE_FIELDS,
        )
        self.assertEqual(fields, {"name": "EDR"})

    def test_absent_fields_are_not_invented(self):
        self.assertEqual(clean_metadata({}, DATASOURCE_METADATA_FIELDS), {})

    def test_rejects_bad_values(self):
        for payload in (
            {"enabled": "maybe"},
            {"sort_order": "first"},
            {"sort_order": "-1"},
            {"stale_threshold_sec": "0"},
            {"stale_threshold_sec": "soon"},
            {"name": "   "},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    clean_metadata(payload, DATASOURCE_METADATA_FIELDS)


class ParseIdListTests(unittest.TestCase):
    def test_form_encoded_json_array_becomes_the_ids(self):
        # What the home page's per-platform Run button sends.
        self.assertEqual(parse_id_list('["a", "b_2"]', "datasource_ids"), {"a", "b_2"})

    def test_comma_separated_text(self):
        self.assertEqual(parse_id_list("a, b ,c", "datasource_ids"), {"a", "b", "c"})

    def test_real_lists_pass_through(self):
        self.assertEqual(parse_id_list(["a", "b"], "datasource_ids"), {"a", "b"})

    def test_absent_or_blank_means_no_restriction(self):
        self.assertIsNone(parse_id_list(None, "datasource_ids"))
        self.assertIsNone(parse_id_list("   ", "datasource_ids"))

    def test_explicit_empty_list_selects_nothing_rather_than_everything(self):
        self.assertEqual(parse_id_list("[]", "datasource_ids"), set())
        self.assertEqual(parse_id_list([], "datasource_ids"), set())

    def test_a_string_is_never_split_into_characters(self):
        self.assertNotIn("[", parse_id_list('["a"]', "datasource_ids"))

    def test_rejects_malformed_values(self):
        for value in ('["a"', 5, [1, 2], '["a", 2]'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    parse_id_list(value, "datasource_ids")

    def test_rejects_ids_that_are_not_plain_identifiers(self):
        for value in ('{"a": 1}', '["../x"]', "a;b c"):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    parse_id_list(value, "datasource_ids")


if __name__ == "__main__":
    unittest.main()
