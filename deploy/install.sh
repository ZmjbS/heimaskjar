#!/usr/bin/env bash
# Install or update heimaskjar on a Raspberry Pi (Raspberry Pi OS / Debian).
# Run from the cloned repository:   sudo deploy/install.sh
# Safe to run again; deploy/update.sh does that after a git pull.
#
#   HEIMASKJAR_PORT   web port (default: the one used last time, else 8080). Give it
#                     after sudo, which drops variables set before it:
#                     sudo HEIMASKJAR_PORT=80 deploy/install.sh
#   SKIP_DEM=1        don't download the elevation file
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# the port from last time, read back from the installed Caddy site
last_port() { sed -n 's/^:\([0-9]\+\) *{.*/\1/p' /etc/caddy/heimaskjar.caddy 2>/dev/null | head -1; }
PORT="${HEIMASKJAR_PORT:-$(last_port)}"
PORT="${PORT:-8080}"
VAR=/var/lib/heimaskjar
ETC=/etc/heimaskjar
DEM_NAME=IslandsDEMv1.0_50x50m_isn2016_zmasl.tif
DEM_URL="https://ftp.natt.is/gisdata/raster/$DEM_NAME"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi
say() { printf '\n== %s\n' "$*"; }

say "System packages"
apt-get update -qq
apt-get install -y -qq python3-venv git curl caddy

say "Service user and folders"
if ! id heimaskjar >/dev/null 2>&1; then
  useradd --system --home-dir "$VAR" --shell /usr/sbin/nologin heimaskjar
fi
install -d -m 755 -o heimaskjar -g heimaskjar "$VAR" "$VAR/data" "$VAR/cache"
install -d -m 755 "$ETC"

say "Python environment ($REPO/venv)"
if [[ ! -x "$REPO/venv/bin/python" ]]; then
  python3 -m venv "$REPO/venv"
fi
"$REPO/venv/bin/pip" install --quiet --upgrade pip
"$REPO/venv/bin/pip" install --quiet -r "$REPO/requirements.txt"

say "Settings ($ETC/heimaskjar.toml)"
if [[ -f "$ETC/heimaskjar.toml" ]]; then
  echo "Keeping the existing settings file."
else
  sed -e "s|^data_dir = .*|data_dir = \"$VAR/data\"|" \
      -e "s|^cache_dir = .*|cache_dir = \"$VAR/cache\"|" \
      -e "s|^dem_file = .*|dem_file = \"$VAR/$DEM_NAME\"|" \
      "$REPO/config/heimaskjar.example.toml" > "$ETC/heimaskjar.toml"
  chmod 644 "$ETC/heimaskjar.toml"
  echo "Created it from the example – edit the family members in it."
fi

if [[ "${SKIP_DEM:-0}" != 1 && ! -f "$VAR/$DEM_NAME" ]]; then
  say "Elevation file (118 MB, for map contours)"
  curl -fL --progress-bar -o "$VAR/$DEM_NAME.part" "$DEM_URL"
  mv "$VAR/$DEM_NAME.part" "$VAR/$DEM_NAME"
  chown heimaskjar:heimaskjar "$VAR/$DEM_NAME"
fi

say "Scheduled jobs (systemd)"
for unit in "$REPO"/deploy/systemd/*.service "$REPO"/deploy/systemd/*.timer; do
  sed "s|@REPO@|$REPO|g" "$unit" > "/etc/systemd/system/$(basename "$unit")"
done
systemctl daemon-reload
for timer in "$REPO"/deploy/systemd/*.timer; do
  systemctl enable --now "$(basename "$timer")"
done

say "Web server (Caddy, port $PORT)"
# who is listening on a port, if anyone (process name, e.g. "apache2")
listener() { ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'users:(("[^"]*' | head -1 | cut -d'"' -f2; }
busy="$(listener "$PORT")"
if [[ -n "$busy" && "$busy" != caddy ]]; then
  echo "Port $PORT is already used by '$busy'. Choose another: HEIMASKJAR_PORT=8081 sudo $0" >&2
  exit 1
fi
sed -e "s|@REPO@|$REPO|g" -e "s|@PORT@|$PORT|g" \
    "$REPO/deploy/caddy/heimaskjar.caddy" > /etc/caddy/heimaskjar.caddy
CF=/etc/caddy/Caddyfile
# The Debian package's own Caddyfile serves a welcome page on port 80. If that's all it
# does, replace it (keeping a copy): otherwise Caddy can't start while e.g. Apache has 80.
stock() {   # the file minus comments, blank lines and our import line, on one line
  sed -e 's/#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$CF" | grep -v '^$' |
    grep -vx 'import /etc/caddy/heimaskjar.caddy' | tr '\n' '|'
}
if [[ "$(stock)" == ":80 {|root * /usr/share/caddy|file_server|}|" ]]; then
  [[ -f "$CF.dist" ]] || cp "$CF" "$CF.dist"
  printf '# Sites served by Caddy. The packaged original is in Caddyfile.dist.\nimport /etc/caddy/heimaskjar.caddy\n' > "$CF"
  echo "Replaced the packaged welcome-page Caddyfile (copy kept as $CF.dist)."
elif ! grep -qx 'import /etc/caddy/heimaskjar.caddy' "$CF"; then
  printf '\nimport /etc/caddy/heimaskjar.caddy\n' >> "$CF"
fi
if grep -qE '^(:80|http://)' "$CF" && [[ -n "$(listener 80)" && "$(listener 80)" != caddy ]]; then
  echo "Warning: your Caddyfile has a site on port 80, which '$(listener 80)' already uses." >&2
fi
caddy validate --config "$CF" --adapter caddyfile >/dev/null
systemctl enable caddy
if ! systemctl restart caddy; then
  journalctl -u caddy -n 15 --no-pager >&2
  exit 1
fi

say "Done"
cat <<EOF
Pages:     http://$(hostname).local:$PORT/   (or http://$(hostname -I | awk '{print $1}'):$PORT/)
Settings:  $ETC/heimaskjar.toml
Next run:  $(systemctl list-timers heimaskjar-vedur.timer --no-legend | awk '{print $1, $2, $3}')

Make today's maps now:   sudo systemctl start heimaskjar-vedur
Follow it:               journalctl -u heimaskjar-vedur -f
EOF
