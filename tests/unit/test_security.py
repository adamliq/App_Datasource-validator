import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeService  # noqa: E402

from app import security  # noqa: E402


class CapabilityTests(unittest.TestCase):
    def test_require_capability_passes_when_present(self):
        security.require_capability({"capabilities": ["dsv_view_results"]}, "dsv_view_results")

    def test_require_capability_raises_when_absent(self):
        with self.assertRaises(security.CapabilityError):
            security.require_capability({"capabilities": []}, "dsv_admin_config")

    def test_require_capability_handles_missing_session(self):
        with self.assertRaises(security.CapabilityError):
            security.require_capability(None, "dsv_admin_config")

    def test_has_capability(self):
        self.assertTrue(security.has_capability({"capabilities": ["a", "b"]}, "a"))
        self.assertFalse(security.has_capability({"capabilities": ["a", "b"]}, "c"))


class CredentialStorageTests(unittest.TestCase):
    def test_get_returns_none_when_unset(self):
        service = FakeService()
        self.assertIsNone(security.get_service_credential(service))

    def test_set_then_get_roundtrips(self):
        service = FakeService()
        security.set_service_credential(service, "svc_dsv@example.com", "hunter2")
        cred = security.get_service_credential(service)
        self.assertEqual(cred["username"], "svc_dsv@example.com")
        self.assertEqual(cred["clear_password"], "hunter2")

    def test_set_replaces_rather_than_duplicates(self):
        service = FakeService()
        security.set_service_credential(service, "svc_dsv@example.com", "first")
        security.set_service_credential(service, "svc_dsv@example.com", "second")
        entries = list(service.storage_passwords)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].clear_password, "second")

    def test_set_rejects_empty_password(self):
        service = FakeService()
        with self.assertRaises(security.ValidationError):
            security.set_service_credential(service, "svc_dsv@example.com", "")

    def test_delete_removes_entry(self):
        service = FakeService()
        security.set_service_credential(service, "svc_dsv@example.com", "hunter2")
        self.assertTrue(security.delete_service_credential(service))
        self.assertIsNone(security.get_service_credential(service))

    def test_delete_returns_false_when_nothing_to_delete(self):
        service = FakeService()
        self.assertFalse(security.delete_service_credential(service))


class ValidateIdentifierTests(unittest.TestCase):
    def test_accepts_simple_identifier(self):
        security.validate_identifier("platform-1_ok", "platform_id")

    def test_rejects_empty(self):
        with self.assertRaises(security.ValidationError):
            security.validate_identifier("", "platform_id")

    def test_rejects_control_characters(self):
        with self.assertRaises(security.ValidationError):
            security.validate_identifier("bad\x01id", "platform_id")

    def test_rejects_path_traversal_like_input(self):
        with self.assertRaises(security.ValidationError):
            security.validate_identifier("../../etc/passwd", "platform_id")

    def test_rejects_too_long(self):
        with self.assertRaises(security.ValidationError):
            security.validate_identifier("a" * 200, "platform_id", max_length=128)

    def test_allow_at_permits_email_username(self):
        security.validate_identifier("svc.dsv@example.com", "username", allow_at=True)


class ValidateDisplayTextTests(unittest.TestCase):
    def test_accepts_normal_text(self):
        security.validate_display_text("Firewall - EU Region", "name")

    def test_rejects_control_characters(self):
        with self.assertRaises(security.ValidationError):
            security.validate_display_text("bad\x00text", "name")

    def test_rejects_non_string(self):
        with self.assertRaises(security.ValidationError):
            security.validate_display_text(123, "name")


class RedactTests(unittest.TestCase):
    def test_redacts_known_secret_fields(self):
        result = security.redact({"username": "svc", "password": "hunter2"})
        self.assertEqual(result["username"], "svc")
        self.assertEqual(result["password"], "***REDACTED***")

    def test_passes_through_non_dict(self):
        self.assertEqual(security.redact("not a dict"), "not a dict")


if __name__ == "__main__":
    unittest.main()
