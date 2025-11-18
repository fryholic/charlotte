"""Regular expressions for the emoji enlarger feature."""

import re

SINGLE_EMOJI_REGEX = re.compile(
    r"""
    ^               # Start of string
    (?!<.*<)        # Negative lookahead to ensure there is not more than one '<' at the beginning
    <               # Emoji opening delimiter
    (a)?            # Optional 'a' for animated emoji
    :               # Colon delimiter
    (.+?)           # Emoji name
    :               # Colon delimiter
    ([0-9]{15,21})  # Emoji ID
    >               # Emoji closing delimiter
    $               # End of string
    """,
    re.VERBOSE,
)
