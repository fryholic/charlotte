"""Command-line entry point."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from dotenv import load_dotenv

from charlotte.app import create_bot
from charlotte.config import ConfigError, load_config
from charlotte.observability import configure_log_level, configure_logging, log_exception


async def _run_bot(
    bot: Any,
    token: str,
    *,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Run Discord until it exits or the container requests a graceful stop."""

    loop = asyncio.get_running_loop()
    stop_requested = shutdown_event or asyncio.Event()
    installed_signals: list[signal.Signals] = []
    for requested_signal in (signal.SIGTERM,):
        try:
            loop.add_signal_handler(requested_signal, stop_requested.set)
        except NotImplementedError, RuntimeError:
            continue
        installed_signals.append(requested_signal)

    bot_task = asyncio.create_task(bot.start(token, reconnect=True))
    stop_waiter = asyncio.create_task(stop_requested.wait())
    try:
        await asyncio.wait((bot_task, stop_waiter), return_when=asyncio.FIRST_COMPLETED)
        if stop_waiter.done() and not bot_task.done():
            await bot.close()
        await bot_task
    finally:
        stop_waiter.cancel()
        await asyncio.gather(stop_waiter, return_exceptions=True)
        await bot.close()
        if not bot_task.done():
            bot_task.cancel()
            await asyncio.gather(bot_task, return_exceptions=True)
        for installed_signal in installed_signals:
            loop.remove_signal_handler(installed_signal)


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
        asyncio.run(_run_bot(bot, config.discord_token.reveal()))
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        log_exception(log, error, event="app.run_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
