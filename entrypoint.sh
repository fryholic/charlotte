#!/bin/bash
# Xvfb 정상 종료를 위한 트랩 설정
trap "pkill -f 'Xvfb :99'; pkill -f 'python3 log_server.py'" EXIT

# Xvfb 시작 (기존 프로세스 제거 후)
pkill -f 'Xvfb :99'
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset > /dev/null 2>&1 &
export DISPLAY=:99

# 로그 서버 시작 (백그라운드로)
python3 log_server.py &

# 추가 지연 시간 추가 (Xvfb 초기화 대기)
sleep 2

# 봇 실행
python3 charlotte_bot.py