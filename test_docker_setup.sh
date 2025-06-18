#!/bin/bash
echo "Building Docker image..."
docker compose build

if [ $? -ne 0 ]; then
  echo "Docker build failed."
  exit 1
fi

echo "---------------------------------------------"
echo "Running deezspot test inside Docker container..."
docker compose run --rm charlotte python test_deezspot_fixed.py

echo "---------------------------------------------"
echo "Test completed. You can now start the container with:"
echo "docker compose up"
