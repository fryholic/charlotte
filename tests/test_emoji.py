from charlotte.extensions.emoji_enlarger import parse_custom_emoji


def test_parses_one_static_or_animated_custom_emoji() -> None:
    static = parse_custom_emoji("  <:hello:12345678901234567> ")
    animated = parse_custom_emoji("<a:hello:123456789012345678>")
    assert static and not static.animated
    assert animated and animated.animated


def test_rejects_unicode_text_and_multiple_custom_emoji() -> None:
    assert parse_custom_emoji("😀") is None
    assert parse_custom_emoji("x <:hello:12345678901234567>") is None
    assert parse_custom_emoji("<:hello:12345678901234567><:world:22345678901234567>") is None
