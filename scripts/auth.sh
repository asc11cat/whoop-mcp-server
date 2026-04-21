#!/usr/bin/env bash
# One-time Whoop OAuth flow. Runs the auth helper inside the container
# image so no local Python venv is needed. Port 8765 is forwarded to the
# host loopback so the browser redirect lands on the listener.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/config/.env"
TOKEN_FILE="$PROJECT_DIR/config/token.json"

if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE (copy config/.env.example and fill in)" >&2
    exit 1
fi

touch "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

exec podman run --rm \
    --cap-drop=ALL \
    --security-opt=no-new-privileges \
    --userns=keep-id \
    --memory=256m --cpus=0.5 --pids-limit=100 \
    -p 127.0.0.1:8765:8765 \
    --env-file "$ENV_FILE" \
    -e WHOOP_TOKEN_PATH=/app/config/token.json \
    -v "$TOKEN_FILE:/app/config/token.json:z" \
    --entrypoint python \
    whoop-mcp-server src/auth.py
