import traceback
import sys
import discord

async def handle_error(ctx, error, user_message="오류가 발생했습니다."):
    """
    예외를 처리하는 중앙 집중식 핸들러
    :param ctx: 디스코드 컨텍스트
    :param error: 발생한 예외 객체
    :param user_message: 사용자에게 보여줄 안내 메시지
    """
    # 상세 에러 로깅 (stdout/stderr)
    print(f"Error occurred in command '{ctx.command}':", file=sys.stderr)
    traceback.print_exc()
    
    # 사용자에게 친절한 메시지 전송
    if ctx and ctx.channel:
        try:
            await ctx.send(f"⚠️ {user_message}")
        except Exception as send_error:
            print(f"Failed to send error message to channel: {send_error}", file=sys.stderr)
