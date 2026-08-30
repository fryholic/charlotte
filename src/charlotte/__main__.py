"""Command-line entry point."""

from __future__ import annotations

import logging

from dotenv import load_dotenv

from charlotte.app import create_bot
from charlotte.config import ConfigError, load_config
from charlotte.observability import configure_log_level, configure_logging, log_exception


def main() -> int:
    load_dotenv()
    try:
        config = load_config()
    except ConfigError as error:
        configure_log_level("INFO")
        log_exception(logging.getLogger("charlotte.startup"), error, event="config.invalid")
        return 2
    configure_logging(config)
    log = logging.getLogger("charlotte.startup")
    log.info(
        "Charlotte starting",
        extra={"event": "app.starting", "environment": config.environment.value},
    )
    for variable in sorted(config.legacy_environment_variables):
        log.warning(
            "deprecated environment variable ignored",
            extra={"event": "config.legacy_environment", "variable": variable},
        )
    bot = create_bot(config)
    try:
        bot.run(config.discord_token.reveal(), log_handler=None)
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        log_exception(log, error, event="app.run_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
