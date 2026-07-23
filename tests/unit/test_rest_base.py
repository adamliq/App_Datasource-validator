import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from app import security  # noqa: E402
from app.models.base import NotFoundError  # noqa: E402
from app.rest_base import DsvPersistentHandler, RestRequest, error_response, json_response  # noqa: E402


class RestRequestParsingTests(unittest.TestCase):
    def test_parses_method_path_query_session(self):
        raw = {
            "method": "get",
            "path": "/datasource_validator/platforms/p1/",
            "query": [["limit", "10"], ["status", "PASS"]],
            "session": {"authtoken": "tok123", "user": "alice"},
            "payload": None,
        }
        req = RestRequest(raw)
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.path_segments, ["datasource_validator", "platforms", "p1"])
        self.assertEqual(req.query, {"limit": "10", "status": "PASS"})
        self.assertEqual(req.session_key, "tok123")
        self.assertEqual(req.user, "alice")
        self.assertEqual(req.payload, {})

    def test_parses_json_string_payload(self):
        raw = {"method": "POST", "payload": json.dumps({"name": "Firewall"})}
        req = RestRequest(raw)
        self.assertEqual(req.payload, {"name": "Firewall"})

    def test_parses_dict_payload_directly(self):
        raw = {"method": "POST", "payload": {"name": "Firewall"}}
        req = RestRequest(raw)
        self.assertEqual(req.payload, {"name": "Firewall"})

    def test_malformed_json_payload_becomes_empty_dict(self):
        raw = {"method": "POST", "payload": "{not json"}
        req = RestRequest(raw)
        self.assertEqual(req.payload, {})

    def test_parses_form_encoded_body_as_list_of_pairs(self):
        # This is what a real form-encoded POST (e.g. the setup page's
        # save button, via splunkjs's service.post()) delivers - Splunk's
        # persistent-connection protocol carries it as `form`, not
        # `payload`, which `payload` alone previously ignored entirely.
        raw = {
            "method": "POST",
            "form": [["username", "svc-dsv"], ["password", "s3cret"]],
        }
        req = RestRequest(raw)
        self.assertEqual(req.payload, {"username": "svc-dsv", "password": "s3cret"})

    def test_parses_form_encoded_body_as_flat_dict(self):
        raw = {"method": "POST", "form": {"username": "svc-dsv"}}
        req = RestRequest(raw)
        self.assertEqual(req.payload, {"username": "svc-dsv"})

    def test_form_and_json_payload_are_merged(self):
        raw = {
            "method": "POST",
            "payload": json.dumps({"name": "Firewall"}),
            "form": [["extra", "1"]],
        }
        req = RestRequest(raw)
        self.assertEqual(req.payload, {"name": "Firewall", "extra": "1"})

    def test_query_accepts_flat_dict_too(self):
        raw = {"method": "GET", "query": {"limit": "10"}}
        req = RestRequest(raw)
        self.assertEqual(req.query, {"limit": "10"})

    def test_missing_method_defaults_to_get(self):
        req = RestRequest({})
        self.assertEqual(req.method, "GET")


class ResponseHelperTests(unittest.TestCase):
    def test_json_response_shape(self):
        resp = json_response({"a": 1}, status=201)
        self.assertEqual(resp["status"], 201)
        self.assertEqual(json.loads(resp["payload"]), {"a": 1})

    def test_error_response_default_status(self):
        resp = error_response("bad input")
        self.assertEqual(resp["status"], 400)
        self.assertEqual(json.loads(resp["payload"]), {"error": "bad input"})


class _ProbeHandler(DsvPersistentHandler):
    """Test double whose handle_get/handle_post are set per-test."""

    behavior = None  # set by each test to a callable(request) -> result

    def handle_get(self, request):
        return self.behavior(request)

    def handle_post(self, request):
        return self.behavior(request)


class DispatchAndExceptionMappingTests(unittest.TestCase):
    def _invoke(self, behavior, method="GET", payload=None):
        handler = _ProbeHandler()
        handler.behavior = behavior
        raw = {"method": method, "path": "", "query": [], "session": {"authtoken": "tok"}, "payload": payload}
        response = handler.handle(json.dumps(raw))
        return response["status"], json.loads(response["payload"])

    def test_successful_plain_dict_returns_200(self):
        status, body = self._invoke(lambda req: {"ok": True})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True})

    def test_successful_tuple_uses_given_status(self):
        status, body = self._invoke(lambda req: ({"created": True}, 201))
        self.assertEqual(status, 201)
        self.assertEqual(body, {"created": True})

    def test_validation_error_maps_to_400(self):
        def raiser(req):
            raise security.ValidationError("bad field")
        status, body = self._invoke(raiser)
        self.assertEqual(status, 400)
        self.assertIn("bad field", body["error"])

    def test_capability_error_maps_to_403(self):
        def raiser(req):
            raise security.CapabilityError("dsv_admin_config")
        status, _ = self._invoke(raiser)
        self.assertEqual(status, 403)

    def test_not_found_error_maps_to_404(self):
        def raiser(req):
            raise NotFoundError("no such platform: p1")
        status, _ = self._invoke(raiser)
        self.assertEqual(status, 404)

    def test_value_error_maps_to_400(self):
        def raiser(req):
            raise ValueError("missing field")
        status, _ = self._invoke(raiser)
        self.assertEqual(status, 400)

    def test_unexpected_exception_maps_to_500_without_leaking_details(self):
        def raiser(req):
            raise RuntimeError("boom, internal secret path /etc/x")
        status, body = self._invoke(raiser)
        self.assertEqual(status, 500)
        self.assertIn("internal error", body["error"])

    def test_unsupported_method_returns_405(self):
        handler = _ProbeHandler()
        handler.behavior = lambda req: {"ok": True}
        raw = {"method": "PATCH", "path": "", "query": [], "session": {}, "payload": None}
        response = handler.handle(json.dumps(raw))
        self.assertEqual(response["status"], 405)

    def test_malformed_request_body_returns_400(self):
        handler = _ProbeHandler()
        response = handler.handle("not json at all")
        self.assertEqual(response["status"], 400)


if __name__ == "__main__":
    unittest.main()
