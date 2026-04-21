#!/usr/bin/env bash
# Build the whoop-mcp-server container image.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"
exec podman build -t whoop-mcp-server -f Containerfile .
