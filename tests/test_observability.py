import json
import logging
from types import SimpleNamespace

import pytest

from charlotte.observability import ErrorContext, ErrorReporter, JsonFormatter, redact, redact_url


def test_redacts_credentials_and_sensitive_query_values() -> None:
    value = redact("token=abc https://www.youtube.com/watch?v=public&signature=secret#fragment")
    assert "abc" not in value
    assert "secret" not in value
    assert "v=public" in value
    assert "fragment" not in value


def test_redacts_soundcloud_secret_path() -> None:
    value = redact_url("https://soundcloud.com/artist/track/s-SecretCode?si=public")
    assert "SecretCode" not in value
    assert "[redacted-secret]" in value


def test_redacts_gateway_session_and_verification_keys_in_mapping_text() -> None:
    value = redact(
        "{'session_id': 'session-secret', 'verify_key': 'verify-secret'} "
        "Authorization: Bot header-secret"
    )
    assert "session-secret" not in value
    assert "verify-secret" not in value
    assert "header-secret" not in value


def test_redacts_sensitive_mapping_values_and_nested_formatter_fields() -> None:
    secrets = {
        "cookie": "session-secret",
        "Authorization": "Bearer header-secret",
        "verify-key": "verify-secret",
        "database_password": "pw",
        "nested": [{"access_token": "nested-token"}],
    }
    redacted = redact(secrets)
    rendered = json.dumps(redacted)
    for secret in ("session-secret", "header-secret", "verify-secret", "pw", "nested-token"):
        assert secret not in rendered

    record = logging.LogRecord("charlotte.test", logging.ERROR, __file__, 1, "failed", (), None)
    record.context = secrets
    payload = JsonFormatter().format(record)
    for secret in ("session-secret", "header-secret", "verify-secret", "pw", "nested-token"):
        assert secret not in payload


def test_redacts_direct_media_url_path_and_query() -> None:
    value = redact_url("https://media.example.net/private/path?expire=1&n=challenge")
    assert "private/path" not in value
    assert "challenge" not in value
    assert "media.example.net" in value


def test_soundcloud_lookalike_host_is_not_treated_as_public() -> None:
    value = redact_url("https://evil-soundcloud.com/private/token-path?expires=1&n=challenge")
    assert "private/token-path" not in value
    assert "challenge" not in value
    assert "evil-soundcloud.com" in value


def test_json_formatter_redacts_registered_token_without_assignment_context() -> None:
    token = "raw.discord.token"
    record = logging.LogRecord(
        "charlotte.test",
        logging.ERROR,
        __file__,
        1,
        f"login failed with {token}",
        (),
        None,
    )
    payload = json.loads(JsonFormatter(secrets=(token,)).format(record))
    assert token not in payload["message"]
    assert "[redacted]" in payload["message"]


def test_owner_dm_preserves_required_fields_and_traceback_within_limit(app_config) -> None:
    reporter = ErrorReporter(app_config, error_id_factory=lambda: "fixed-error-id")
    error = RuntimeError("failure " + "x" * 1000)
    body = reporter._render_dm(
        "fixed-error-id",
        "provider.failed",
        error,
        "y" * 1500 + " traceback-tail",
        ErrorContext(
            guild_name="guild" * 100,
            guild_id=1,
            url="https://www.youtube.com/watch?v=public&signature=private",
        ),
    )
    assert len(body) <= 1900
    assert "Environment: development" in body
    assert "Error ID: fixed-error-id" in body
    assert "Traceback:" in body
    assert "traceback-tail" in body
    assert "private" not in body


@pytest.mark.asyncio
async def test_error_reporter_never_raises_for_unprintable_exception(app_config) -> None:
    class BrokenError(RuntimeError):
        def __str__(self) -> str:
            raise RuntimeError("broken str")

    sent = []

    async def send(body, **kwargs):
        sent.append((body, kwargs))

    reporter = ErrorReporter(app_config, error_id_factory=lambda: "fixed-error-id")
    reporter._owner = SimpleNamespace(send=send)
    error_id = await reporter.report(BrokenError(), event="test.broken")
    assert error_id == "fixed-error-id"
    assert len(sent) == 1
    assert "unprintable" in sent[0][0]
    assert "test-token" not in sent[0][0]


def test_owner_dm_redacts_raw_configured_token(app_config) -> None:
    reporter = ErrorReporter(app_config)
    body = reporter._render_dm(
        "fixed-error-id",
        "test.secret",
        RuntimeError("test-token"),
        "trace contains test-token",
        ErrorContext(message_content="test-token"),
    )
    assert "test-token" not in body
    assert body.count("[redacted]") >= 2
