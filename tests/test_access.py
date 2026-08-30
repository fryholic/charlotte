from types import SimpleNamespace

from charlotte.music.access import AccessReason, decide_control, decide_play


def member(user_id=1, channel=None, administrator=False):
    return SimpleNamespace(
        id=user_id,
        voice=SimpleNamespace(channel=channel) if channel is not None else None,
        guild_permissions=SimpleNamespace(administrator=administrator),
    )


def test_regular_user_must_share_the_bot_voice_channel() -> None:
    bot_channel = object()
    decision = decide_control(member(channel=object()), bot_channel, frozenset())
    assert not decision.allowed
    assert decision.reason is AccessReason.DIFFERENT_VOICE_CHANNEL


def test_administrator_and_operator_can_control_remotely() -> None:
    bot_channel = object()
    assert decide_control(
        member(channel=None, administrator=True), bot_channel, frozenset()
    ).allowed
    assert decide_control(member(user_id=42, channel=None), bot_channel, frozenset({42})).allowed


def test_even_privileged_play_requires_a_target_voice_channel() -> None:
    decision = decide_play(member(user_id=42, channel=None), object(), frozenset({42}))
    assert not decision.allowed
    assert decision.privileged
    assert decision.reason is AccessReason.USER_NOT_IN_VOICE
