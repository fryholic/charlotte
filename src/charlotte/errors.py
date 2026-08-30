"""Errors that cross application boundaries."""


class CharlotteError(Exception):
    """Base class for expected, external, and internal failures."""


class UserError(CharlotteError):
    message_id = "common.command_failed"

    def __init__(self, message_id: str | None = None) -> None:
        if message_id is not None:
            self.message_id = message_id
        super().__init__(self.message_id)


class UserInputError(UserError):
    pass


class AccessDeniedError(UserError):
    pass


class UnsupportedSourceError(UserError):
    message_id = "music.play.unsupported_host"


class UnsupportedContentError(UserError):
    pass


class SourceUnavailableError(CharlotteError):
    """A supported remote source could not be read without authentication."""

    def __init__(self, message_id: str, detail: str = "") -> None:
        self.message_id = message_id
        super().__init__(detail or message_id)


class ProviderError(CharlotteError):
    pass


class PlaybackError(CharlotteError):
    pass


class QueueLimitError(UserError):
    message_id = "common.operation_cancelled"


class ExtensionOperationError(CharlotteError):
    pass
