#!/usr/bin/env bash
# Ephemeral MCP launcher for whoop-mcp-server.
# Spawned by the MCP host (Claude Code) via .mcp.json.
#
# Reads OAuth app creds from config/.env and the rotating refresh-token
# bundle from config/token.json (mounted rw so the server can persist
# refreshed tokens). Network is allowed — the Whoop API requires
# outbound HTTPS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/config/.env"
TOKEN_FILE="$PROJECT_DIR/config/token.json"

if [ ! -f "$ENV_FILE" ]; then
    echo "Missing $ENV_FILE (copy config/.env.example and fill in)" >&2
    exit 1
fi
if [ ! -s "$TOKEN_FILE" ]; then
    echo "Missing or empty $TOKEN_FILE — run scripts/auth.sh first" >&2
    exit 1
fi

exec podman run -i --rm \
    --cap-drop=ALL \
    --security-opt=no-new-privileges \
    --userns=keep-id \
    --memory=256m --cpus=0.5 --pids-limit=100 \
    --read-only --tmpfs /tmp \
    --env-file "$ENV_FILE" \
    -e WHOOP_TOKEN_PATH=/app/config/token.json \
    -v "$TOKEN_FILE:/app/config/token.json:z" \
    whoop-mcp-server
