"""
Structured JSON logging for the Data Source Validator app.

Writes one JSON object per line to
$SPLUNK_HOME/var/log/splunk/datasource_validator_<component>.log, which
default/props.conf parses (sourcetype dsv:internal) once Splunk's standard
internal log monitor picks the file up into _internal. This is the only
filesystem write this app performs outside of KV Store and
storage/passwords, and it targets Splunk's own designated log directory -
never an arbitrary path, never anything under the read-only app bundle.

No shell, no subprocess, no OS-level calls beyond stdlib logging/pathlib.
"""
import json
import logging
import logging.handlers
import os
import socket
import threading
import traceback
from pathlib import Path

_LOG_FILE_PREFIX = "datasource_validator"
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 5

_lock = threading.Lock()
_loggers = {}


def _splunk_home():
    env_home = os.environ.get("SPLUNK_HOME")
    if env_home:
        return Path(env_home)
    # Fallback for local/manual use: this file lives at
    # $SPLUNK_HOME/etc/apps/datasource_validator/bin/app/logging_utils.py
    return Path(__file__).resolve().parents[5]


def _log_dir(log_dir=None):
    if log_dir is not None:
        return Path(log_dir)
    return _splunk_home() / "var" / "log" / "splunk"


class JsonFormatter(logging.Formatter):
    """Formats each LogRecord as a single-line JSON object."""

    def __init__(self, component):
        super().__init__()
        self._component = component
        self._hostname = socket.gethostname()

    def format(self, record):
        payload = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "component": self._component,
            "host": self._hostname,
            "message": record.getMessage(),
        }
        fields = getattr(record, "dsv_fields", None)
        if fields:
            payload["fields"] = fields
        if record.exc_info:
            payload["exception"] = "".join(
                traceback.format_exception(*record.exc_info)
            ).strip()
        return json.dumps(payload, default=str, sort_keys=True)

    def formatTime(self, record, datefmt=None):
        import datetime

        dt = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc
        )
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class DsvLogger:
    """
    Thin wrapper around a stdlib Logger that requires structured fields
    to be passed as keyword arguments rather than string-formatted into
    the message, so every log line is reliably machine-parseable.
    """

    def __init__(self, logger):
        self._logger = logger

    def debug(self, message, **fields):
        self._log(logging.DEBUG, message, fields)

    def info(self, message, **fields):
        self._log(logging.INFO, message, fields)

    def warning(self, message, **fields):
        self._log(logging.WARNING, message, fields)

    def error(self, message, exc_info=False, **fields):
        self._log(logging.ERROR, message, fields, exc_info=exc_info)

    def _log(self, level, message, fields, exc_info=False):
        self._logger.log(
            level, message, extra={"dsv_fields": fields or None}, exc_info=exc_info
        )


def get_logger(component, log_dir=None):
    """
    Return a DsvLogger for the given component name (e.g. "worker",
    "rest_config", "query_validator"). Safe to call repeatedly - the
    underlying handler is created once per (component, log_dir) pair.
    """
    key = (component, str(log_dir) if log_dir else None)
    with _lock:
        existing = _loggers.get(key)
        if existing is not None:
            return existing

        base_logger = logging.getLogger(f"dsv.{component}")
        base_logger.setLevel(logging.INFO)
        base_logger.propagate = False

        if not base_logger.handlers:
            directory = _log_dir(log_dir)
            directory.mkdir(parents=True, exist_ok=True)
            file_path = directory / f"{_LOG_FILE_PREFIX}_{component}.log"
            handler = logging.handlers.RotatingFileHandler(
                str(file_path), maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setFormatter(JsonFormatter(component))
            base_logger.addHandler(handler)

        wrapped = DsvLogger(base_logger)
        _loggers[key] = wrapped
        return wrapped
