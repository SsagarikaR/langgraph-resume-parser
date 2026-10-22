import json
import logging

from app.core.logging import JsonFormatter, RequestIdFilter, request_id


def record(message="hello"):
    rec = logging.LogRecord("app.test", logging.INFO, __file__, 1, message, None, None)
    RequestIdFilter().filter(rec)
    return rec


def test_log_records_carry_the_current_request_id():
    assert record().request_id == "-"  # outside a request
    token = request_id.set("req-1")
    try:
        assert record().request_id == "req-1"
    finally:
        request_id.reset(token)


def test_json_format_is_one_object_per_line():
    token = request_id.set("req-2")
    try:
        line = JsonFormatter().format(record("screening started"))
    finally:
        request_id.reset(token)
    entry = json.loads(line)
    assert entry["message"] == "screening started"
    assert entry["request_id"] == "req-2"
    assert entry["level"] == "INFO"
    assert "\n" not in line
