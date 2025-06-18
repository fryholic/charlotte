import sys
import os
from pathlib import Path

# deezspot 패키지 경로 추가
deezspot_path = Path('/home/pi/deezspot')
sys.path.append(str(deezspot_path))

print("Testing deezspot imports...")
try:
    import deezspot
    from deezspot.spotloader import SpoLogin
    print("✅ Imported deezspot main module")
    
    from deezspot.libutils.utils import link_is_valid, get_ids
    print("✅ Imported link_is_valid and get_ids")
    
    from deezspot.spotloader.__spo_api__ import tracking
    print("✅ Imported tracking")
    
    from deezspot.deezloader.__download__ import Download_JOB
    print("✅ Imported Download_JOB")
    
    from deezspot.deezloader.__utils__ import check_track_ids
    print("✅ Imported check_track_ids")
    
    print("✅ All deezspot imports successful")
    
    # Check if crypto modules are available
    try:
        from hashlib import md5
        from binascii import a2b_hex, b2a_hex
        from Crypto.Cipher.Blowfish import new as newBlowfish, MODE_CBC
        from Crypto.Cipher.AES import new as newAES, MODE_ECB
        print("✅ All crypto modules available")
    except ImportError as e:
        print(f"❌ Crypto module import failed: {e}")
        
except ImportError as e:
    print(f"❌ Import failed: {e}")
    
# Check if credentials.json exists
credentials_path = '/home/pi/charlotte/credentials.json'
if os.path.exists(credentials_path):
    print(f"✅ Credentials file found at {credentials_path}")
else:
    print(f"❌ Credentials file not found at {credentials_path}")
    
print("Test completed.")
