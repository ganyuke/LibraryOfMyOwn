#!/usr/bin/env bash
# Install LibraryOfMyOwn under /opt/archive on a Raspberry Pi (aarch64).
# Bundles typst and pandoc; pdf-scripts ship with the repo.
#
# Usage (on the Pi, from a git checkout):
#   sudo ARCHIVE_SRC=/path/to/Archive ./scripts/deploy-pi.sh
#
# Optional:
#   PDF_SCRIPTS_SRC=/path/to/pdf-scripts override pdf-scripts source (default: $ARCHIVE_SRC/pdf-scripts)
#   INSTALL_ROOT=/opt/archive          install location (default)
#   ARCHIVE_USER=archive                 system user (created if missing)

set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/archive}"
ARCHIVE_USER="${ARCHIVE_USER:-archive}"
ARCHIVE_SRC="${ARCHIVE_SRC:-$(cd "$(dirname "$0")/.." && pwd)}"
PDF_SCRIPTS_SRC="${PDF_SCRIPTS_SRC:-$ARCHIVE_SRC/pdf-scripts}"

TYPST_URL="${TYPST_URL:-https://github.com/typst/typst/releases/download/v0.15.1/typst-aarch64-unknown-linux-musl.tar.xz}"
PANDOC_URL="${PANDOC_URL:-https://github.com/jgm/pandoc/releases/download/3.11/pandoc-3.11-linux-arm64.tar.gz}"

APP_DIR="$INSTALL_ROOT/app"
VENV_DIR="$INSTALL_ROOT/venv"
BIN_DIR="$INSTALL_ROOT/bin"
DATA_DIR="$INSTALL_ROOT/data"
PDF_SCRIPTS_DIR="$INSTALL_ROOT/pdf-scripts"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

if [[ ! -f "$ARCHIVE_SRC/requirements.txt" ]]; then
  echo "ARCHIVE_SRC must point at the LibraryOfMyOwn repository root (missing requirements.txt)." >&2
  exit 1
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

need_cmd curl
need_cmd tar
need_cmd python3
need_cmd install
need_cmd rsync

python_version() {
  python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
}

PY_VER="$(python_version)"
PY_MAJOR="${PY_VER%%.*}"
PY_MINOR="${PY_VER#*.}"
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 12 ]]; then
  echo "Warning: Python $PY_VER found; this project targets 3.14+. Install a newer python3 if the app fails." >&2
fi

echo "==> Creating layout under $INSTALL_ROOT"
install -d -m 755 "$INSTALL_ROOT" "$BIN_DIR" "$DATA_DIR" "$PDF_SCRIPTS_DIR"

if ! id "$ARCHIVE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$INSTALL_ROOT" --shell /usr/sbin/nologin "$ARCHIVE_USER"
fi

echo "==> Installing typst"
tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
curl -fsSL "$TYPST_URL" | tar -xJ -C "$tmpdir"
typst_bin="$(find "$tmpdir" -name typst -type f | head -n 1)"
if [[ -z "$typst_bin" ]]; then
  echo "Could not find typst binary in $TYPST_URL" >&2
  exit 1
fi
install -m 755 "$typst_bin" "$BIN_DIR/typst"

echo "==> Installing pandoc"
rm -rf "$tmpdir"/*
curl -fsSL "$PANDOC_URL" | tar -xz -C "$tmpdir"
pandoc_bin="$(find "$tmpdir" -path '*/bin/pandoc' -type f | head -n 1)"
if [[ -z "$pandoc_bin" ]]; then
  echo "Could not find pandoc binary in $PANDOC_URL" >&2
  exit 1
fi
install -m 755 "$pandoc_bin" "$BIN_DIR/pandoc"

echo "==> Syncing application to $APP_DIR"
install -d -m 755 "$APP_DIR"
rsync -a --delete \
  --exclude '.env' \
  --exclude '.venv/' \
  --exclude 'venv/' \
  --exclude 'data/' \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude 'OPERATOR.md' \
  "$ARCHIVE_SRC/" "$APP_DIR/"

echo "==> Python virtualenv"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements-pdf-print.txt"

if [[ -d "$PDF_SCRIPTS_SRC" ]]; then
  echo "==> Copying pdf-scripts"
  rsync -a --delete "$PDF_SCRIPTS_SRC/" "$PDF_SCRIPTS_DIR/"
else
  echo "Warning: pdf-scripts not found at $PDF_SCRIPTS_SRC (PDF export will be disabled until you copy them)." >&2
fi

echo "==> Data directory"
install -d -m 755 "$DATA_DIR/pdf-cache"
if [[ ! -d "$DATA_DIR/stories.git" ]]; then
  git init --bare "$DATA_DIR/stories.git"
fi
if [[ ! -f "$DATA_DIR/site.json" ]]; then
  if [[ -f "$APP_DIR/data/site.json.example" ]]; then
    cp "$APP_DIR/data/site.json.example" "$DATA_DIR/site.json"
  else
    echo '{}' >"$DATA_DIR/site.json"
  fi
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "==> Creating $APP_DIR/.env from deploy/pi.env.example"
  cp "$APP_DIR/deploy/pi.env.example" "$APP_DIR/.env"
  echo "Review $APP_DIR/.env (paths only; secrets and public URL are set at /setup)." >&2
fi

echo "==> Installing systemd unit"
sed "s|/opt/archive|$INSTALL_ROOT|g" "$APP_DIR/deploy/archive.service" >/etc/systemd/system/archive.service
systemctl daemon-reload
systemctl enable archive.service

chown -R "$ARCHIVE_USER:$ARCHIVE_USER" "$INSTALL_ROOT"

echo
echo "Deploy complete."
echo "  App:         $APP_DIR"
echo "  Data:        $DATA_DIR"
echo "  Tools:       $BIN_DIR/typst $BIN_DIR/pandoc"
echo "  Environment: $APP_DIR/.env"
echo
"$BIN_DIR/typst" --version
"$BIN_DIR/pandoc" --version | head -n 1
echo
echo "Next steps:"
echo "  1. Edit $APP_DIR/.env (paths only; secrets auto-generate on first start)"
echo "  2. sudo systemctl start archive"
echo "  3. Open https://your.domain/setup in a browser"
echo "  4. Point Caddy (see examples/caddy/README.md) at 127.0.0.1:8000"
