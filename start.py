#!/usr/bin/env python3
"""
Tom Wood Workshop — Start Server
Run this file to launch the application.
Requires: Python 3.8+ and `pip install -r requirements.txt`
"""
import subprocess
import sys
import os
import time
import webbrowser

from env_config import load_env

load_env()

try:
    from db.schema import DB_PATH
except ModuleNotFoundError:
    from db_schema import DB_PATH

PORT = int(os.getenv("PORT", "8484"))
URL  = f"http://localhost:{PORT}"

print()
print("━" * 54)
print("  Tom Wood Workshop System")
print("━" * 54)
print()

# Check Python version
if sys.version_info < (3, 8):
    print("  ✗ Python 3.8 or higher required.")
    sys.exit(1)

# Reset DB if --reset flag given
if "--reset" in sys.argv:
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("  ✓ Database reset")

# Start server
server_path = os.path.join(os.path.dirname(__file__), "server.py")
proc = subprocess.Popen([sys.executable, server_path])

time.sleep(1.2)

print(f"  ✓ Server running at {URL}")
print(f"  ✓ API ready at {URL}/api/orders")
print()
print("  Opening browser…")
print("  Press Ctrl+C to stop.")
print()

try:
    opened = webbrowser.open(URL)
    if not opened:
        print(f"  Open {URL} manually in your browser.")
    proc.wait()
except KeyboardInterrupt:
    proc.terminate()
    print()
    print("  Server stopped.")
except Exception:
    print(f"  Open {URL} manually in your browser.")
    proc.wait()
