#!/bin/bash

# 필요한 의존성 설치
pip install pycryptodome spotipy spotipy-anon tqdm fastapi uvicorn[standard]

# deezspot 디렉토리가 있는지 확인
if [ -d "/deezspot" ]; then
  echo "Installing deezspot from /deezspot directory..."
  # deezspot 디렉토리가 존재하면 개발 모드로 설치
  cd /deezspot
  pip install -e .
  cd /app
else
  echo "ERROR: /deezspot directory not found!"
fi

# Spotify API 환경변수 확인
if [ -z "$SPOTIFY_CLIENT_ID" ] || [ -z "$SPOTIFY_CLIENT_SECRET" ]; then
  echo "WARNING: Spotify API credentials not found in environment variables!"
  
  # 환경 변수가 없는 경우 .env 파일에서 로드 시도
  if [ -f ".env" ]; then
    echo "Trying to load from .env file..."
    export $(grep -v '^#' .env | xargs)
  fi
  
  # 그래도 없는 경우 하드코딩된 값을 사용 (필요한 경우에만)
  if [ -z "$SPOTIFY_CLIENT_ID" ] || [ -z "$SPOTIFY_CLIENT_SECRET" ]; then
    echo "Using default Spotify credentials (not recommended)"
    export SPOTIFY_CLIENT_ID="e54ae3a1e90940ca9c53abcef61d3183"
    export SPOTIFY_CLIENT_SECRET="1ab6b7c396c14331b3c8c0bf2a06e1ca"
  fi
fi

echo "SPOTIFY_CLIENT_ID=${SPOTIFY_CLIENT_ID:0:5}... is set"
echo "SPOTIFY_CLIENT_SECRET=${SPOTIFY_CLIENT_SECRET:0:5}... is set"

# 인증 파일 확인
if [ ! -f "/app/credentials.json" ] && [ -f "/deezspot/credentials.json" ]; then
  echo "Copying credentials.json from /deezspot to /app"
  cp /deezspot/credentials.json /app/
fi

# Charlotte 봇 실행
echo "Starting Charlotte bot: $@"
python "$@"
