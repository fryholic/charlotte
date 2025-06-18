import asyncio
import sys
import os
from pathlib import Path

# Add parent directory to path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Modules.TrackFactory import TrackFactory, DEEZER_AVAILABLE, CRYPTO_AVAILABLE

async def main():
    print(f"DEEZER_AVAILABLE: {DEEZER_AVAILABLE}")
    print(f"CRYPTO_AVAILABLE: {CRYPTO_AVAILABLE}")
    
    # Test with a Spotify URL
    spotify_url = "https://open.spotify.com/track/4UQy41kC5LjzwQuiuWOpwA?si=ae5550856d844a0f"
    print(f"\nTesting with Spotify URL: {spotify_url}")
    
    try:
        sources = await TrackFactory.identify_source(spotify_url)
        if sources:
            print(f"✅ Successfully created source with title: {sources[0].title}")
        else:
            print("❌ No sources returned")
    except Exception as e:
        print(f"❌ Error creating source: {str(e)}")
    
    # Test with a YouTube URL
    youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    print(f"\nTesting with YouTube URL: {youtube_url}")
    
    try:
        sources = await TrackFactory.identify_source(youtube_url)
        if sources:
            print(f"✅ Successfully created source with title: {sources[0].title}")
        else:
            print("❌ No sources returned")
    except Exception as e:
        print(f"❌ Error creating source: {str(e)}")
    
    # Test with a search query
    search_query = "Rick Astley Never Gonna Give You Up"
    print(f"\nTesting with search query: {search_query}")
    
    try:
        sources = await TrackFactory.identify_source(search_query)
        if sources:
            print(f"✅ Successfully created source with title: {sources[0].title}")
        else:
            print("❌ No sources returned")
    except Exception as e:
        print(f"❌ Error creating source: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())
