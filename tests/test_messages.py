from types import SimpleNamespace

from charlotte.messages import MESSAGES, duration_text, queue_embed, render


def test_approved_copy_is_stable() -> None:
    assert render("music.play.multiple_attachments") == "오디오 파일은 하나만 보내 주세요!"
    assert render("extension.protected", name="music_commands") == (
        "이 Extension은 실행 중에 내릴 수 없습니다: music_commands"
    )
    assert "다른 영상 URL" in render("music.youtube.unavailable")
    assert "다른 트랙 URL" in render("music.soundcloud.unavailable")
    assert "music.youtube.first_item_used" not in MESSAGES


def test_queue_embed_shows_current_plus_only_four_upcoming() -> None:
    current = SimpleNamespace(title="current", requester="one", duration=65, paused=False)
    upcoming = [
        SimpleNamespace(title=f"track-{index}", requester="two", duration=None)
        for index in range(1, 5)
    ]
    embed = queue_embed(current, upcoming)
    assert embed.title == "🎧 재생 대기열"
    assert len(embed.fields) == 2
    assert "current" in embed.fields[0].value
    assert "track-4" in embed.fields[1].value
    assert "페이지" not in embed.fields[1].value


def test_queue_embed_fields_stay_within_discord_limits() -> None:
    current = SimpleNamespace(title="c" * 1000, requester="r" * 1000, duration=1e20, paused=True)
    upcoming = [
        SimpleNamespace(title="t" * 1000, requester="u" * 1000, duration=1e20) for _ in range(4)
    ]
    embed = queue_embed(current, upcoming)
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert duration_text(float("inf")) is None
