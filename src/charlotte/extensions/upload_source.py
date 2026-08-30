from charlotte.extensions.contract import ExtensionKind, ExtensionMetadata
from charlotte.providers.upload import UploadProvider

EXTENSION_META = ExtensionMetadata(
    name="upload_source",
    kind=ExtensionKind.SOURCE,
    provider_name="upload",
    load_order=22,
)


async def setup(bot) -> None:
    bot.providers.register(UploadProvider())


async def teardown(bot) -> None:
    bot.providers.unregister("upload")
