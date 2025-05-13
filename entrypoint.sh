#!/bin/bash
# Xvfb 정상 종료를 위한 트랩 설정
trap "pkill -f 'Xvfb :99'" EXIT

# Xvfb 시작 (기존 프로세스 제거 후)
pkill -f 'Xvfb :99'
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset > /dev/null 2>&1 &
export DISPLAY=:99

# 추가 지연 시간 추가 (Xvfb 초기화 대기)
sleep 2

"$@"