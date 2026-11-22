"""
Covers tools/build_package.py's choice of files to ship. A built .spl is
committed at the repo root (the app root), so it must never be picked up
as part of the next package - an earlier build packed the previous .spl
inside itself and still reported success.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

import build_package  # noqa: E402


class ShippableFilesTests(unittest.TestCase):
    def _shipped(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in files:
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
            with mock.patch.object(build_package, "APP_ROOT", root):
                return sorted(str(p.relative_to(root)) for p in build_package._shippable_files())

    def test_committed_spl_is_not_packaged(self):
        shipped = self._shipped([
            "app.manifest",
            "default/app.conf",
            "datasource_validator-1.1.0.spl",
            "releases/datasource_validator-1.0.9.spl",
        ])
        self.assertEqual(shipped, ["app.manifest", "default/app.conf"])

    def test_development_files_are_still_excluded(self):
        shipped = self._shipped([
            "bin/rest_runs.py",
            "bin/__pycache__/rest_runs.cpython-311.pyc",
            "tests/unit/test_x.py",
            "tools/build_package.py",
            "HANDOFF.md",
            ".gitignore",
        ])
        self.assertEqual(shipped, ["bin/rest_runs.py"])


if __name__ == "__main__":
    unittest.main()
