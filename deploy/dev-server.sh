#!/usr/bin/env bash
# Try the pages on your own machine: serves web/ with data/ under it, like Caddy does.
#   deploy/dev-server.sh [port]      then open http://localhost:8000/
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$REPO/data"
ln -sfn ../data "$REPO/web/data"          # web/data is ignored by git
cd "$REPO/web"
exec python3 -m http.server "${1:-8000}"
