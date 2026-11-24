"""
Guards the two settings that decide whether Splunk runs the validation
worker at all. Both were wrong in 1.0.9 - 1.1.1 and neither AppInspect nor
the offline validator notices:

* README/inputs.conf.spec: a stanza that lists no parameter line is
  silently ignored, so the input type is never registered and enabling it
  fails with HTTP 404 (reported on a real Splunk Cloud stack).
* The scheme's use_single_instance: when true, splunkd starts one
  long-lived process and ignores `interval`, but the worker does one tick
  and exits, so it ran once at startup and never again.
"""
import re
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))

import dsv_validation_worker  # noqa: E402


def _spec_stanzas(text):
    """Returns {stanza header: [parameter lines]}; `*` and indented lines are descriptions."""
    stanzas, current = {}, None
    for line in text.splitlines():
        if not line.strip() or line.startswith(("*", " ", "\t", "#")):
            continue
        header = re.fullmatch(r"\[(.+)\]", line.strip())
        if header:
            current = stanzas.setdefault(header.group(1), [])
        elif current is not None and "=" in line:
            current.append(line.strip())
    return stanzas


class SpecFileTests(unittest.TestCase):
    def setUp(self):
        self.stanzas = _spec_stanzas((ROOT / "README" / "inputs.conf.spec").read_text(encoding="utf-8"))

    def test_the_worker_input_is_declared(self):
        self.assertIn("dsv_validation_worker://<name>", self.stanzas)

    def test_every_modular_input_stanza_lists_a_parameter(self):
        for header, params in self.stanzas.items():
            with self.subTest(stanza=header):
                self.assertTrue(
                    params,
                    f"[{header}] lists no parameter line, so Splunk will not register this input",
                )

    def test_the_spec_stanza_matches_the_inputs_conf_scheme(self):
        inputs_conf = (ROOT / "default" / "inputs.conf").read_text(encoding="utf-8")
        self.assertIn("[dsv_validation_worker://main]", inputs_conf)


class SchemeTests(unittest.TestCase):
    def setUp(self):
        self.scheme = ET.fromstring(dsv_validation_worker.SCHEME_XML)

    def test_scheme_is_well_formed(self):
        self.assertEqual(self.scheme.tag, "scheme")
        self.assertTrue(self.scheme.findtext("title"))

    def test_runs_once_per_interval_not_as_a_single_long_lived_instance(self):
        self.assertEqual(self.scheme.findtext("use_single_instance"), "false")


if __name__ == "__main__":
    unittest.main()
