from charlotte.observability import ErrorContext, ErrorReporter, redact, redact_url


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


def test_redacts_direct_media_url_path_and_query() -> None:
    value = redact_url("https://media.example.net/private/path?expire=1&n=challenge")
    assert "private/path" not in value
    assert "challenge" not in value
    assert "media.example.net" in value


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
