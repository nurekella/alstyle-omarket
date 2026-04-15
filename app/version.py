import os
import time

VERSION = "2.2.0"
BUILD_ID = os.environ.get("BUILD_ID") or str(int(time.time()))
ASSET_TAG = f"{VERSION}.{BUILD_ID}"
