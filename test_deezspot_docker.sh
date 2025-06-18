#!/bin/bash

echo "Rebuilding and testing the Docker container..."
echo "-----------------------------------------"

# 이미지 다시 빌드
docker compose down
docker compose build

# 컨테이너 시작
echo "-----------------------------------------"
echo "Starting container in interactive mode..."
docker compose run --rm charlotte bash -c "pip install spotipy && python -c \"
import sys
import os
from pathlib import Path

# deezspot 패키지 경로 추가
print('Checking deezspot availability:')
deezspot_path = Path('/deezspot')
sys.path.append(str(deezspot_path))

try:
    import spotipy
    print('✅ spotipy module is installed')
    
    import deezspot
    print('✅ deezspot module is installed')
    
    from deezspot.spotloader import SpoLogin
    print('✅ deezspot.spotloader module is installed')
    
    from deezspot.libutils.utils import link_is_valid, get_ids
    print('✅ deezspot.libutils.utils functions are available')
    
    from deezspot.spotloader.__spo_api__ import tracking
    print('✅ deezspot.spotloader.__spo_api__.tracking is available')
    
    from deezspot.deezloader.__download__ import Download_JOB
    print('✅ deezspot.deezloader.__download__.Download_JOB is available')
    
    from deezspot.deezloader.__utils__ import check_track_ids
    print('✅ deezspot.deezloader.__utils__.check_track_ids is available')
    
    print('\\n✅ All deezspot modules are available! deezspot functionality should work.')
    
except Exception as e:
    print(f'❌ Error: {e}')
    print('\\n❌ Some deezspot modules are not available.')
\""

echo "-----------------------------------------"
echo "Testing completed. You can now start the bot with:"
echo "docker compose up"
