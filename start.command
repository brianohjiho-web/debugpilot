#!/bin/bash
# DebugPilot for Mac. Double-click this file, or run: bash start.command
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; assert sys.version_info >= (3, 10)" 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo
  echo " Python 3.10 or newer isn't installed."
  echo " Install it from https://www.python.org/downloads/ and run this again."
  echo
  read -n 1 -s -r -p "Press any key to close"
  exit 1
fi

echo
echo " DebugPilot is setting up. The first time takes a few minutes..."
echo
"$PY" -m pip install -r requirements.txt || { echo " Setup failed."; read -n 1 -s -r -p "Press any key"; exit 1; }
"$PY" -m playwright install chromium || { echo " Setup failed."; read -n 1 -s -r -p "Press any key"; exit 1; }

echo
echo " Starting DebugPilot. Your browser will open in a few seconds."
echo " Keep this window open while you use it. Close it to stop."
echo
(sleep 6; open "http://localhost:8000/scan") &
"$PY" -m uvicorn server:app --port 8000
