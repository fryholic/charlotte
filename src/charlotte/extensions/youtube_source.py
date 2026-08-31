from charlotte.extensions.contract import ExtensionKind, ExtensionMetadata
from charlotte.providers.youtube import YouTubeProvider

EXTENSION_META = ExtensionMetadata(
    name="youtube_source",
    kind=ExtensionKind.SOURCE,
    provider_name="youtube",
    load_order=20,
)


async def setup(bot) -> None:
    bot.providers.register(YouTubeProvider())


async def teardown(bot) -> None:
    bot.providers.unregister("youtube")
