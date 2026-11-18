#!/usr/bin/env bash
# Fetch the latest version from GitHub and reinstall:   deploy/update.sh
# (run as your own user; it asks for sudo for the install step)
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
git pull --ff-only
sudo SKIP_DEM="${SKIP_DEM:-0}" HEIMASKJAR_PORT="${HEIMASKJAR_PORT:-8080}" "$REPO/deploy/install.sh"
