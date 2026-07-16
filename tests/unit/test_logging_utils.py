import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))

from app import logging_utils  # noqa: E402


class LoggingUtilsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self._tmp.name)
        logging_utils._loggers.clear()

    def tearDown(self):
        for logger in logging_utils._loggers.values():
            for handler in list(logger._logger.handlers):
                handler.close()
                logger._logger.removeHandler(handler)
        logging_utils._loggers.clear()
        self._tmp.cleanup()

    def _read_lines(self, component):
        path = self.log_dir / f"datasource_validator_{component}.log"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_writes_json_line_with_expected_fields(self):
        logger = logging_utils.get_logger("worker", log_dir=self.log_dir)
        logger.info("dispatched search", run_id="run_1", datasource_id="ds_1")

        lines = self._read_lines("worker")
        self.assertEqual(len(lines), 1)
        record = lines[0]
        self.assertEqual(record["message"], "dispatched search")
        self.assertEqual(record["level"], "INFO")
        self.assertEqual(record["component"], "worker")
        self.assertEqual(record["fields"], {"run_id": "run_1", "datasource_id": "ds_1"})
        self.assertTrue(record["timestamp"].endswith("Z"))

    def test_get_logger_is_idempotent_per_component_and_dir(self):
        first = logging_utils.get_logger("worker", log_dir=self.log_dir)
        second = logging_utils.get_logger("worker", log_dir=self.log_dir)
        self.assertIs(first, second)

    def test_different_components_write_different_files(self):
        logging_utils.get_logger("worker", log_dir=self.log_dir).info("a")
        logging_utils.get_logger("rest_config", log_dir=self.log_dir).info("b")
        self.assertTrue((self.log_dir / "datasource_validator_worker.log").exists())
        self.assertTrue((self.log_dir / "datasource_validator_rest_config.log").exists())

    def test_error_level_and_no_fields_omits_fields_key(self):
        logger = logging_utils.get_logger("worker", log_dir=self.log_dir)
        logger.error("something broke")
        record = self._read_lines("worker")[0]
        self.assertEqual(record["level"], "ERROR")
        self.assertNotIn("fields", record)


if __name__ == "__main__":
    unittest.main()
