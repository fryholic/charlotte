from charlotte.extensions.contract import ExtensionKind, ExtensionMetadata
from charlotte.providers.soundcloud import SoundCloudProvider

EXTENSION_META = ExtensionMetadata(
    name="soundcloud_source",
    kind=ExtensionKind.SOURCE,
    provider_name="soundcloud",
    load_order=21,
)


async def setup(bot) -> None:
    bot.providers.register(SoundCloudProvider())


async def teardown(bot) -> None:
    bot.providers.unregister("soundcloud")
