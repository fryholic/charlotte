# Charlotte

Charlotte is a self-hosted Discord bot for music playback and custom emoji enlargement. It is designed for a small number of personal servers while keeping every guild's voice connection, current track, and queue completely independent.

## Features

- Independent, concurrent playback in multiple Discord guilds
- YouTube videos, YouTube Shorts, SoundCloud tracks, and Discord audio uploads
- In-memory queues and uploads with no application database or temporary media files
- Prefix commands with same-voice-channel control and administrator/operator exceptions
- Automatic enlargement of a message containing exactly one custom Discord emoji
- Runtime-discovered source Extensions with operator-controlled load, unload, and reload
- Linux Docker deployment for amd64 and arm64

## Supported inputs and limitations

| Input | Supported | Important limits |
|---|---|---|
| YouTube | Public normal video and Shorts URLs | Search, YouTube Music, live/upcoming streams, cookies, and login are not supported. A playlist URL uses only its physical first entry. |
| SoundCloud | An unauthenticated single-track URL, including supported share and secret links | Search, profiles, sets, and playlists are not supported. |
| Discord upload | Exactly one attachment that FFmpeg can read as audio | The attachment stays in memory. Charlotte adds no size limit beyond Discord and optional operator-configured queue memory limits. |

Unlisted or restricted media works only when `yt-dlp` can access it without authentication. A restart intentionally discards every queue and upload buffer.

The public `queue` response shows at most five items in play order: the current track plus four upcoming tracks, or five upcoming tracks when nothing is current. It has no buttons or pagination.

## Commands

The production prefix defaults to `?`; development defaults to `!`. `bot.command_prefix` in TOML is the only override.

| Command | Description |
|---|---|
| `?play <URL>` | Play one supported YouTube or SoundCloud URL. |
| `?play` with one attachment | Play one uploaded audio file. |
| `?skip` | Skip the current track, including while paused. |
| `?queue` | Show the current track and the next items, up to five total. |
| `?stop` | Stop playback and clear the queue while remaining connected. |
| `?leave` | Clear playback and disconnect. |
| `?pause` / `?resume` | Pause or resume the current track. |
| `?help` | Show the public command summary. |

## Permissions

Enable the **Message Content Intent** for the bot in the Discord Developer Portal. Charlotte requests only guild, guild-message, message-content, and voice-state intents.

Grant the bot these channel permissions where the corresponding feature is used:

- View Channels, Send Messages, Embed Links, and Read Message History
- Send Messages in Threads when emoji enlargement is enabled in threads
- Connect, Speak, and Use Voice Activity for music
- Manage Messages for emoji enlargement

A regular user can control music only while sharing the bot's voice channel. A Discord administrator or a user listed in `bot.operator_user_ids` may run music controls remotely. `play` always requires the caller to be in the target voice channel; a privileged caller can clear the old playback and move Charlotte there. Runtime Extension commands are restricted to configured operators, not all administrators.

## Requirements

- Docker Engine with Compose on a Linux amd64 or arm64 host
- A Discord bot token
- Discord Message Content Intent enabled

The image contains Python 3.14.7, FFmpeg, Node.js, and the yt-dlp EJS challenge scripts required for current YouTube extraction. Host installations of these tools are not required for Docker deployment.

## Quick start

```bash
git clone https://github.com/fryholic/charlotte.git
cd charlotte
cp .env.example .env
cp config.example.toml config.toml
```

Put the production token in `.env`, review `config.toml`, then run:

```bash
docker compose up -d --build
docker compose logs --tail=200 charlotte
```

Normal updates keep the existing workflow:

```bash
git pull
docker compose up -d --build
```

## Configuration

Secrets and instance selection come from the environment. All non-secret bot behavior except the two optional memory limits comes from TOML.

| Environment variable | Meaning |
|---|---|
| `DISCORD_TOKEN` | Production bot token. |
| `DISCORD_TOKEN_DEV` | Development bot token when `DEV=true`. |
| `DEV` | Strict boolean selecting development defaults and token. |
| `CHARLOTTE_CONFIG` | TOML path; Compose sets this to `/app/config.toml`. |
| `MAX_QUEUE_TRACKS` | Maximum upcoming tracks per guild; omitted or `0` means unlimited. |
| `MAX_QUEUED_UPLOAD_BYTES` | Maximum in-memory upload bytes owned by one guild; omitted or `0` means unlimited. |

Example TOML:

```toml
[bot]
command_prefix = "?"
operator_user_ids = [123456789012345678]

[extensions]
startup_required = [
  "music_commands",
  "youtube_source",
  "soundcloud_source",
  "upload_source",
  "emoji_enlarger",
]
startup_optional = []

[emoji]
enabled = true
allowed_channel_ids = []
```

An empty emoji allowlist permits every visible channel where Charlotte has all required permissions. Threads inherit the policy of their parent channel. Actual `.env`, `.env.dev`, `config.toml`, and `config.dev.toml` files are ignored by both Git and the Docker build context.

## Production and development instances

Keep production and development in separate fixed checkouts or worktrees. Do not switch branches inside a live deployment directory. The production checkout follows `main`; the development checkout follows `dev`.

Create development secrets and configuration without committing them:

```bash
cp config.dev.example.toml config.dev.toml
printf 'DISCORD_TOKEN_DEV=replace-me\n' > .env.dev
```

Run development alongside production with its own fixed container name, token, prefix, and emoji allowlist:

```bash
docker compose -p charlotte-dev \
  --env-file .env.dev \
  -f docker-compose.yml \
  -f docker-compose.dev.yml \
  up -d --build
```

Neither Compose configuration bind-mounts source code. A code change takes effect only after rebuilding the image.

## Extension operations

Charlotte discovers Extension modules from its package at startup. A name in `extensions.startup_required` must load successfully or startup fails; a failure in `startup_optional` disables only that Extension.

Configured operators can use the hidden prefix group:

```text
?extension list
?extension load <name>
?extension unload <name>
?extension reload <name>
```

Startup requirement and runtime protection are separate. The core and `music_commands` cannot be unloaded at runtime. A source that was required at startup may be unloaded or reloaded only when every guild has no current/queued track and the provider has no in-flight inspection or preparation.

## Logging and privacy

Charlotte writes UTC JSON Lines to stdout/stderr. Compose uses Docker's `json-file` driver with five 10 MiB files. Production defaults to INFO and development to DEBUG.

Unexpected runtime and external-service errors are logged with a UUID and full traceback, then sent directly to the Discord application owner with a length-limited traceback. Duplicate DMs are suppressed in memory for five minutes. Startup and shutdown failures remain log-only.

Diagnostic context can include guild/channel names and IDs, command/provider/track identifiers, requester display name and ID, public source URL, upload filename, and—only for emoji failures—the message content, author display name, message ID, and emoji ID. Tokens, cookies, authorization data, signatures, sensitive query values, URL user info, fragments, and SoundCloud secret path tokens are redacted. Ordinary users never receive exception details or diagnostic UUIDs.

The Docker healthcheck reports Gateway readiness and heartbeat freshness for diagnostics. An unhealthy status alone does not terminate the bot process.

## License

Charlotte is available under the [MIT License](LICENSE).

Copyright (c) 2025-2026 fryholic
