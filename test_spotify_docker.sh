#!/bin/bash

# Docker 컨테이너에서 Spotify API 테스트 실행
echo "Building the Docker image..."
docker compose build

echo "Running Spotify API test in Docker container..."
docker compose run --rm charlotte python test_spotify_api.py
