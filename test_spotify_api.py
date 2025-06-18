import sys
import os
from pathlib import Path
import json

# deezspot 패키지 경로 추가
deezspot_path = Path('/home/pi/deezspot')
sys.path.append(str(deezspot_path))

print("Testing Spotify API initialization...")

try:
    from deezspot.easy_spoty import Spo
    
    # 환경 변수 또는 하드코딩된 값에서 클라이언트 ID 및 시크릿 읽기
    client_id = os.getenv('SPOTIFY_CLIENT_ID', 'e54ae3a1e90940ca9c53abcef61d3183')
    client_secret = os.getenv('SPOTIFY_CLIENT_SECRET', '1ab6b7c396c14331b3c8c0bf2a06e1ca')
    
    print(f"Using client_id={client_id[:5]}...")
    
    # Spo 클래스 초기화
    Spo.__init__(client_id=client_id, client_secret=client_secret)
    print("✅ Successfully initialized Spo class")
    
    # 테스트 트랙 ID
    test_track_id = "4UQy41kC5LjzwQuiuWOpwA"
    print(f"Fetching track info for ID: {test_track_id}")
    
    # 트랙 정보 가져오기
    track_info = Spo.get_track(test_track_id)
    print("✅ Successfully retrieved track info:")
    print(f"  - Title: {track_info.get('name', 'Unknown')}")
    print(f"  - Artist: {track_info.get('artists', [{}])[0].get('name', 'Unknown')}")
    print(f"  - Album: {track_info.get('album', {}).get('name', 'Unknown')}")
    
    # 상세 메타데이터 추출 테스트
    from deezspot.spotloader.__spo_api__ import tracking
    metadata = tracking(test_track_id)
    
    if metadata:
        print("✅ Successfully retrieved track metadata via tracking:")
        print(f"  - Title: {metadata.get('music', 'Unknown')}")
        print(f"  - Artist: {metadata.get('artist', 'Unknown')}")
        print(f"  - Album: {metadata.get('album', 'Unknown')}")
    else:
        print("❌ Failed to retrieve track metadata via tracking")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    
print("\nTest completed.")
