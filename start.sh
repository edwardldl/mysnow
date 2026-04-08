#!/bin/bash

# MySnow Auto-Start Script
echo "❄️ Starting MySnow..."

# Get the directory of this script
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Start Python server in the background
# Output is redirected to a log file
python3 -m http.server 8080 > server.log 2>&1 &
SERVER_PID=$!

# Wait a moment for server to initialize
sleep 2

# Open the site in the default browser
echo "🚀 Opening browser at http://localhost:8080"
open "http://localhost:8080"

echo "✅ Server running on PID $SERVER_PID. To stop it, run: kill $SERVER_PID"
echo "---"
# Keep terminal alive and show logs
tail -f server.log
