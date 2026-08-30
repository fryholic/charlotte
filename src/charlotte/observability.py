"""UTC JSON logging, redaction, and direct application-owner error reports."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import discord

from charlotte.config import AppConfig
from charlotte.constants import ERROR_SUPPRESSION_WINDOW, OWNER_DM_TIMEOUT

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(token|cookie|authorization|password|secret|signature|sig|key|session_id)=([^\s&]+)"
)
_SECRET_MAPPING = re.compile(
    r"(?i)(['\"]?(?:token|cookie|authorization|password|secret|signature|sig|key|session_id|verify_key)['\"]?\s*:\s*['\"])([^'\"]+)"
)
_SECRET_HEADER = re.compile(r"(?i)\b(authorization|cookie|set-cookie)\s*:\s*([^\r\n,}]+)")
_URL = re.compile(r"https?://[^\s<>'\",\]\)]+")
_SENSITIVE_QUERY_KEYS = {
    "auth",
    "authorization",
    "cookie",
    "key",
    "password",
    "sig",
    "signature",
    "token",
}
_PUBLIC_URL_HOSTS = {
    "discord.com",
    "m.soundcloud.com",
    "m.youtube.com",
    "on.soundcloud.com",
    "soundcloud.com",
    "www.soundcloud.com",
    "www.youtube.com",
    "youtu.be",
    "youtube.com",
}
_MAX_SUPPRESSION_FINGERPRINTS = 1024


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "[redacted-url]"
    if not parsed.scheme or not parsed.netloc:
        return value
    safe_query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _SENSITIVE_QUERY_KEYS or any(
            marker in lowered for marker in ("token", "signature", "auth", "cookie")
        ):
            safe_query.append((key, "[redacted]"))
        else:
            safe_query.append((key, item))
    hostname = parsed.hostname or ""
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return "[redacted-url]"
    netloc = f"{hostname}{port}"
    path = parsed.path
    if hostname.lower().endswith("soundcloud.com"):
        path = "/".join(
            "[redacted-secret]" if segment.startswith("s-") else segment
            for segment in path.split("/")
        )
    elif hostname.lower() not in _PUBLIC_URL_HOSTS and (path or safe_query):
        path = "/[redacted-path]" if path else ""
        safe_query = [(key, "[redacted]") for key, _ in safe_query]
    return urlunsplit((parsed.scheme, netloc, path, urlencode(safe_query), ""))


def redact(value: object, *, secrets: tuple[str, ...] = ()) -> object:
    if isinstance(value, str):
        scrubbed = value
        for secret in secrets:
            if secret:
                scrubbed = scrubbed.replace(secret, "[redacted]")
        scrubbed = _URL.sub(lambda match: redact_url(match.group(0)), scrubbed)
        scrubbed = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", scrubbed)
        scrubbed = _SECRET_MAPPING.sub(lambda match: f"{match.group(1)}[redacted]", scrubbed)
        scrubbed = _SECRET_HEADER.sub(lambda match: f"{match.group(1)}: [redacted]", scrubbed)
        return scrubbed
    if isinstance(value, dict):
        return {str(key): redact(item, secrets=secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, secrets=secrets) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    try:
        rendered = str(value)
    except Exception:
        rendered = f"<{type(value).__name__}: unprintable>"
    return redact(rendered, secrets=secrets)


class JsonFormatter(logging.Formatter):
    """One JSON object per record, with stable keys for future ingestion."""

    _standard = frozenset(logging.makeLogRecord({}).__dict__)

    def __init__(
        self,
        *,
        environment: str | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.environment = environment
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if self.environment is not None:
            payload["environment"] = self.environment
        for key, value in record.__dict__.items():
            if key not in self._standard and key not in {"message", "asctime"}:
                payload[key] = value
        if record.exc_info and "traceback" not in payload:
            payload["traceback"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(
            redact(payload, secrets=self.secrets),
            ensure_ascii=False,
            separators=(",", ":"),
        )


def configure_logging(config: AppConfig) -> None:
    configure_log_level(
        config.log_level,
        environment=config.environment.value,
        secrets=(config.discord_token.reveal(),),
    )


def configure_log_level(
    level: str,
    *,
    environment: str | None = None,
    secrets: tuple[str, ...] = (),
) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(environment=environment, secrets=secrets))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for noisy_logger in ("asyncio", "discord", "yt_dlp"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
    logging.captureWarnings(True)


def log_exception(
    logger: logging.Logger,
    error: BaseException,
    *,
    event: str,
    context: dict[str, object] | None = None,
    level: int = logging.ERROR,
    error_id: str | None = None,
) -> str:
    """Synchronously log one caught exception with common diagnostic fields."""

    error_id = error_id or str(uuid.uuid4())
    logger.log(
        level,
        "caught exception",
        extra={
            "event": event,
            "error_id": error_id,
            "context": context or {},
            "exception_type": type(error).__name__,
            "traceback": _format_exception_safe(error),
        },
    )
    return error_id


@dataclass(frozen=True, slots=True)
class ErrorContext:
    guild_name: str | None = None
    guild_id: int | None = None
    channel_name: str | None = None
    channel_id: int | None = None
    command: str | None = None
    provider: str | None = None
    track_id: str | None = None
    requester_name: str | None = None
    requester_id: int | None = None
    url: str | None = None
    filename: str | None = None
    message_content: str | None = None
    author_name: str | None = None
    message_id: int | None = None
    emoji_id: int | None = None

    def compact(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class ErrorReporter:
    def __init__(
        self,
        config: AppConfig,
        *,
        error_id_factory: Callable[[], object] = uuid.uuid4,
    ) -> None:
        self.config = config
        self._error_id_factory = error_id_factory
        self.log = logging.getLogger("charlotte.errors")
        self._owner: discord.abc.User | None = None
        self._last_sent: dict[str, tuple[float, int]] = {}
        self._secrets = (config.discord_token.reveal(),)

    async def resolve_owner(self, bot: discord.Client) -> bool:
        try:
            info = await bot.application_info()
            self._owner = info.owner
            return self._owner is not None
        except Exception as error:
            log_exception(self.log, error, event="reporter.owner_failed")
            return False

    def expected(
        self,
        event: str,
        *,
        context: ErrorContext | None = None,
        error: BaseException | None = None,
    ) -> None:
        details = (context or ErrorContext()).compact()
        if error is None:
            self.log.info(
                "expected request failure",
                extra={"event": event, "context": details},
            )
            return
        log_exception(self.log, error, event=event, context=details, level=logging.INFO)

    async def report(
        self,
        error: BaseException,
        *,
        event: str,
        context: ErrorContext | None = None,
        notify_owner: bool = True,
    ) -> str:
        details = context or ErrorContext()
        try:
            error_id = str(self._error_id_factory())
        except Exception:
            error_id = str(uuid.uuid4())
        full_traceback = _format_exception_safe(error)
        try:
            error_id = log_exception(
                self.log,
                error,
                event=event,
                context=details.compact(),
                error_id=error_id,
            )
        except Exception:
            self.log.error(
                "error reporting log fallback",
                extra={"event": "reporter.log_failed", "error_id": error_id},
            )
        if notify_owner and self._owner is not None:
            try:
                if not self._should_send(event, error, full_traceback):
                    return error_id
                body = self._render_dm(error_id, event, error, full_traceback, details)
                async with asyncio.timeout(OWNER_DM_TIMEOUT):
                    await self._owner.send(body, allowed_mentions=discord.AllowedMentions.none())
                self.log.info(
                    "owner error DM sent", extra={"event": "reporter.dm_sent", "error_id": error_id}
                )
            except BaseException as dm_error:
                if isinstance(dm_error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                try:
                    log_exception(
                        self.log,
                        dm_error,
                        event="reporter.dm_failed",
                        context={"origin_error_id": error_id},
                        level=logging.WARNING,
                    )
                except Exception:
                    self.log.warning(
                        "owner error DM failed",
                        extra={"event": "reporter.dm_failed", "error_id": error_id},
                    )
        return error_id

    def _should_send(self, event: str, error: BaseException, full_traceback: str) -> bool:
        tail = full_traceback.rsplit("\n", 4)[-4:]
        fingerprint = hashlib.sha256(
            f"{event}|{type(error).__qualname__}|{tail}".encode()
        ).hexdigest()
        now = time.monotonic()
        previous = self._last_sent.get(fingerprint)
        stale = [key for key, (sent_at, _) in self._last_sent.items() if now - sent_at > 600]
        for key in stale:
            self._last_sent.pop(key, None)
        if previous is not None and now - previous[0] < ERROR_SUPPRESSION_WINDOW:
            repeat_count = previous[1] + 1
            self._last_sent[fingerprint] = (previous[0], repeat_count)
            self.log.info(
                "owner error DM suppressed",
                extra={
                    "event": "reporter.dm_suppressed",
                    "fingerprint": fingerprint,
                    "repeat_count": repeat_count,
                },
            )
            return False
        if len(self._last_sent) >= _MAX_SUPPRESSION_FINGERPRINTS:
            oldest = min(self._last_sent, key=lambda key: self._last_sent[key][0])
            self._last_sent.pop(oldest, None)
        self._last_sent[fingerprint] = (now, 0)
        return True

    def _render_dm(
        self,
        error_id: str,
        event: str,
        error: BaseException,
        full_traceback: str,
        context: ErrorContext,
    ) -> str:
        now = datetime.now(UTC).isoformat()
        mandatory_fields: list[tuple[str, object]] = [
            ("Environment", self.config.environment.value),
            ("Error ID", error_id),
            ("Time", now),
            ("Event", event),
        ]
        context_fields: list[tuple[str, object | None]] = [
            ("Guild", _named_id(context.guild_name, context.guild_id)),
            ("Channel", _named_id(context.channel_name, context.channel_id)),
            ("Command", context.command),
            ("Provider", context.provider),
            ("Track", context.track_id),
            ("Requester", _named_id(context.requester_name, context.requester_id)),
            ("URL", context.url),
            ("Filename", context.filename),
            ("Message", context.message_content),
            ("Author", context.author_name),
            ("Message ID", context.message_id),
            ("Emoji ID", context.emoji_id),
        ]
        mandatory = "\n".join(f"{label}: {_one_line(value)}" for label, value in mandatory_fields)
        context_text = "\n".join(
            f"{label}: {_one_line(value)}" for label, value in context_fields if value is not None
        )
        context_text = _truncate(str(redact(context_text, secrets=self._secrets)), 650)
        exception_summary = _truncate(
            str(
                redact(
                    f"{type(error).__name__}: {_one_line(error)}",
                    secrets=self._secrets,
                )
            ),
            220,
        )
        sections = ["Charlotte에서 오류가 발생했습니다.", mandatory]
        if context_text:
            sections.append(context_text)
        sections.append(f"Exception: {exception_summary}")
        prefix = "\n".join(sections) + "\n\nTraceback:\n"
        remaining = max(0, 1900 - len(prefix))
        safe_traceback = str(redact(full_traceback, secrets=self._secrets))[-remaining:]
        return prefix + safe_traceback


def _named_id(name: str | None, snowflake: int | None) -> str | None:
    if name is None and snowflake is None:
        return None
    if name is None:
        return str(snowflake)
    if snowflake is None:
        return name
    return f"{name} ({snowflake})"


def _one_line(value: object) -> str:
    try:
        rendered = str(value)
    except Exception:
        rendered = f"<{type(value).__name__}: unprintable>"
    return rendered.replace("\r", " ").replace("\n", " ")


def _format_exception_safe(error: BaseException) -> str:
    try:
        return "".join(traceback.format_exception(error))
    except Exception:
        return f"{type(error).__module__}.{type(error).__qualname__}: <unprintable exception>"


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"
