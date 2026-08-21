#!/usr/bin/env bash
# Start the hike routes web server
# Usage: ./start.sh [port]
# Defaults to port 8082

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/output"
PORT="${1:-8082}"

if [ ! -d "$OUTPUT_DIR" ]; then
    echo "Error: output directory not found at $OUTPUT_DIR"
    echo "Run 'python3 generate.py' first."
    exit 1
fi

# Check if port is already in use
if command -v ss >/dev/null 2>&1; then
    if ss -tln | grep -q ":${PORT} "; then
        echo "Port $PORT is already in use."
        exit 1
    fi
elif command -v netstat >/dev/null 2>&1; then
    if netstat -tln | grep -q ":${PORT} "; then
        echo "Port $PORT is already in use."
        exit 1
    fi
fi

echo "Starting Hike Routes server on http://0.0.0.0:${PORT}"
echo "Open http://localhost:${PORT} in your browser"
cd "$OUTPUT_DIR" && python3 -m http.server "$PORT"