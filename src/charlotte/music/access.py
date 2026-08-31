"""Voice-channel access decisions shared by all music commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AccessReason(StrEnum):
    ALLOWED = "allowed"
    USER_NOT_IN_VOICE = "user_not_in_voice"
    DIFFERENT_VOICE_CHANNEL = "different_voice_channel"


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    privileged: bool
    reason: AccessReason


def is_privileged(member: Any, operator_user_ids: frozenset[int]) -> bool:
    administrator = bool(
        getattr(getattr(member, "guild_permissions", None), "administrator", False)
    )
    return administrator or getattr(member, "id", None) in operator_user_ids


def decide_play(member: Any, bot_channel: Any, operator_user_ids: frozenset[int]) -> AccessDecision:
    privileged = is_privileged(member, operator_user_ids)
    user_channel = getattr(getattr(member, "voice", None), "channel", None)
    if user_channel is None:
        return AccessDecision(False, privileged, AccessReason.USER_NOT_IN_VOICE)
    if bot_channel is not None and bot_channel != user_channel and not privileged:
        return AccessDecision(False, privileged, AccessReason.DIFFERENT_VOICE_CHANNEL)
    return AccessDecision(True, privileged, AccessReason.ALLOWED)


def decide_control(
    member: Any, bot_channel: Any, operator_user_ids: frozenset[int]
) -> AccessDecision:
    privileged = is_privileged(member, operator_user_ids)
    if privileged:
        return AccessDecision(True, True, AccessReason.ALLOWED)
    user_channel = getattr(getattr(member, "voice", None), "channel", None)
    if user_channel is None:
        return AccessDecision(False, False, AccessReason.USER_NOT_IN_VOICE)
    if bot_channel != user_channel:
        return AccessDecision(False, False, AccessReason.DIFFERENT_VOICE_CHANNEL)
    return AccessDecision(True, False, AccessReason.ALLOWED)
