#!/bin/bash
# Development server startup script (Git Bash / Linux / macOS)
# Kills previous instances and starts API + UI servers

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "Stopping previous instances..."

# Kill processes on ports 8000 and 5173
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    # Windows Git Bash
    for port in 8000 5173 5174 5175 5176; do
        pid=$(netstat -ano 2>/dev/null | grep ":$port " | grep LISTENING | awk '{print $5}' | head -1)
        if [ -n "$pid" ]; then
            taskkill //F //PID "$pid" 2>/dev/null || true
        fi
    done
else
    # Linux/macOS
    pkill -f "uvicorn src.api.main" 2>/dev/null || true
    pkill -f "vite" 2>/dev/null || true
fi

sleep 1

echo ""
echo "========================================="
echo "  API: http://localhost:8000"
echo "  UI:  http://localhost:5173"
echo "========================================="
echo "Press Ctrl+C to stop both servers"
echo ""

# Start API in background
python -m uvicorn src.api.main:app --reload --host 0.0.0.0 --port 8000 &
API_PID=$!

# Start UI in background
(cd ui && npm run dev) &
UI_PID=$!

# Trap Ctrl+C to kill both
cleanup() {
    echo ""
    echo "Shutting down..."
    kill $API_PID 2>/dev/null
    kill $UI_PID 2>/dev/null
    # Also kill child processes on Windows
    if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
        taskkill //F //PID $API_PID 2>/dev/null || true
        taskkill //F //PID $UI_PID 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM

wait
