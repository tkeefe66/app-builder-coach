import io
import json
import logging
import urllib.error

from reporters import usage as reporter


class Usage:
    """Stand-in for the Anthropic SDK's usage object (attributes, not keys)."""
    input_tokens = 10
    output_tokens = 5
    cache_read_input_tokens = 100
    cache_creation_input_tokens = 2


def test_build_payload_from_object():
    payload = reporter.build_payload("my-app", "claude-haiku-4-5", Usage(), ts="2026-08-11T00:00:00Z")
    assert payload == {"app": "my-app", "model": "claude-haiku-4-5",
                       "ts": "2026-08-11T00:00:00Z", "input_tokens": 10,
                       "output_tokens": 5, "cache_read_input_tokens": 100,
                       "cache_creation_input_tokens": 2}


def test_build_payload_from_dict_and_missing_fields():
    payload = reporter.build_payload("my-app", "m", {"input_tokens": 3},
                                     ts="2026-08-11T00:00:00Z")
    assert payload["input_tokens"] == 3
    assert payload["cache_read_input_tokens"] == 0


def test_report_swallows_transport_errors(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise OSError("network down")
    monkeypatch.setattr(reporter, "_post", boom)
    with caplog.at_level(logging.WARNING):
        reporter.report("my-app", "m", Usage(), url="http://x", token="t", blocking=True)  # must not raise
    assert "network down" in caplog.text


def test_report_logs_4xx_rejection_without_raising(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise urllib.error.HTTPError(
            "http://x", 400, "Bad Request", {},
            io.BytesIO(b'{"detail": "unknown app: not-a-real-app"}'))
    monkeypatch.setattr(reporter, "_post", boom)
    with caplog.at_level(logging.WARNING):
        reporter.report("my-app", "m", Usage(), url="http://x", token="t", blocking=True)  # must not raise
    assert "400" in caplog.text
    assert "unknown app: not-a-real-app" in caplog.text


def test_report_noop_without_config(monkeypatch):
    calls = []
    monkeypatch.setattr(reporter, "_post", lambda *a, **k: calls.append(a))
    reporter.report("my-app", "m", Usage(), url=None, token=None)
    assert calls == []


def test_report_posts_expected_body(monkeypatch):
    seen = {}
    monkeypatch.setattr(reporter, "_post",
                        lambda url, token, body: seen.update(url=url, token=token,
                                                             body=json.loads(body)))
    reporter.report("my-app", "claude-haiku-4-5", Usage(), url="http://x/api/usage",
                    token="tok", blocking=True)
    assert seen["url"] == "http://x/api/usage"
    assert seen["token"] == "tok"
    assert seen["body"]["app"] == "my-app"
    assert seen["body"]["cache_read_input_tokens"] == 100
