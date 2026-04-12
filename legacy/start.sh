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

# Cleanup function to kill the server
cleanup() {
    echo ""
    echo "🛑 Stopping server..."
    kill $SERVER_PID 2>/dev/null || true
    # Also kill any other process on port 8080 just in case
    lsof -ti:8080 | xargs kill -9 2>/dev/null || true
    exit 0
}

# Set trap to catch Ctrl+C (SIGINT) and call cleanup
trap cleanup SIGINT SIGTERM

# Wait a moment for server to initialize
sleep 2

# Open the site in the default browser
echo "🚀 Opening browser at http://localhost:8080"
open "http://localhost:8080"

echo "✅ Server running on PID $SERVER_PID"
echo "ℹ️  Press Ctrl+C to stop the server"
echo "---"
# Keep terminal alive and show logs
tail -f server.log
