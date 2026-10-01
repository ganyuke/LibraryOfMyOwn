#!/usr/bin/env bash
# Install or update LibraryOfMyOwn under /opt/libmyown on Linux.
# Everything it needs (Python, typst, pandoc) is installed alongside it.
#
# Usage (from an existing clone):
#   sudo ./scripts/deploy-pi.sh [-y]
#
# Bootstrap on a fresh system (no clone yet):
#   curl -fsSL https://raw.githubusercontent.com/ganyuke/LibraryOfMyOwn/main/scripts/deploy-pi.sh | sudo bash
#
# Options:
#   -y, --yes    answer yes to every question (for unattended runs)
#   -h, --help   show this help
#
# Optional:
#   REPO_URL=git@github.com:ganyuke/LibraryOfMyOwn.git  git remote (default: GitHub HTTPS or origin of this checkout)
#   GIT_REF=main                                        branch to deploy (default: main)
#   INSTALL_ROOT=/opt/libmyown                          install location (default)
#   LIBMYOWN_USER=libmyown                              system user (created if missing)
#   TYPST_VERSION=0.15.1                                typst release (default)
#   PANDOC_VERSION=3.11                                 pandoc release (default)
#   UV_VERSION=0.12.20                                  uv release (default)
#   TYPST_URL=...                                       override typst download URL
#   PANDOC_URL=...                                      override pandoc download URL
#   UV_URL=...                                          override uv download URL
#   TYPST_SHA256=... PANDOC_SHA256=... UV_SHA256=...    expected download checksum (required with another version or URL)

set -euo pipefail

ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    -y | --yes) ASSUME_YES=1 ;;
    -h | --help)
      sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null \
        || echo "Usage: deploy-pi.sh [-y]"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

# Ask on the terminal even when the script itself arrives on stdin (curl | bash).
confirm() {
  local reply
  if [[ "$ASSUME_YES" -eq 1 ]]; then
    return 0
  fi
  if ! { exec 3</dev/tty; } 2>/dev/null; then
    echo "$1 No terminal to ask on, so stopping here. Run again with -y to continue." >&2
    return 1
  fi
  read -r -p "$1 [y/N] " reply <&3 || reply=""
  exec 3<&-
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

# Remember this script's contents before `git pull` can replace the file.
SELF_SUM=""
if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
  SELF_SUM="$(sha256sum < "${BASH_SOURCE[0]}")"
fi

INSTALL_ROOT="${INSTALL_ROOT:-/opt/libmyown}"
LIBMYOWN_USER="${LIBMYOWN_USER:-libmyown}"
GIT_REF="${GIT_REF:-main}"
DEFAULT_REPO_URL="https://github.com/ganyuke/LibraryOfMyOwn.git"
TYPST_VERSION="${TYPST_VERSION:-0.15.1}"
PANDOC_VERSION="${PANDOC_VERSION:-3.11}"
UV_VERSION="${UV_VERSION:-0.12.20}"

APP_DIR="$INSTALL_ROOT/app"
VENV_DIR="$INSTALL_ROOT/venv"
BIN_DIR="$INSTALL_ROOT/bin"
DATA_DIR="$INSTALL_ROOT/data"
PDF_SCRIPTS_DIR="$INSTALL_ROOT/pdf-scripts"
PYTHON_DIR="$INSTALL_ROOT/python"
UV_CACHE="$INSTALL_ROOT/cache/uv"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run as root: sudo $0" >&2
  exit 1
fi

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

default_typst_url() {
  local asset
  case "$(uname -m)" in
    x86_64) asset="typst-x86_64-unknown-linux-musl.tar.xz" ;;
    aarch64 | arm64) asset="typst-aarch64-unknown-linux-musl.tar.xz" ;;
    armv7l | armv6l) asset="typst-armv7-unknown-linux-musleabi.tar.xz" ;;
    riscv64) asset="typst-riscv64gc-unknown-linux-gnu.tar.xz" ;;
    *)
      echo "Unsupported architecture for typst: $(uname -m) (set TYPST_URL manually)" >&2
      return 1
      ;;
  esac
  printf 'https://github.com/typst/typst/releases/download/v%s/%s\n' "$TYPST_VERSION" "$asset"
}

default_pandoc_url() {
  local arch
  case "$(uname -m)" in
    x86_64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *)
      echo "Unsupported architecture for pandoc $PANDOC_VERSION: $(uname -m) (prebuilt tarballs exist for amd64 and arm64 only; set PANDOC_URL manually)" >&2
      return 1
      ;;
  esac
  printf 'https://github.com/jgm/pandoc/releases/download/%s/pandoc-%s-linux-%s.tar.gz\n' \
    "$PANDOC_VERSION" "$PANDOC_VERSION" "$arch"
}

default_uv_url() {
  local target
  case "$(uname -m)" in
    x86_64) target="x86_64-unknown-linux-gnu" ;;
    aarch64 | arm64) target="aarch64-unknown-linux-gnu" ;;
    armv7l) target="armv7-unknown-linux-gnueabihf" ;;
    armv6l) target="arm-unknown-linux-musleabihf" ;;
    riscv64) target="riscv64gc-unknown-linux-gnu" ;;
    *)
      echo "Unsupported architecture for uv: $(uname -m) (set UV_URL manually)" >&2
      return 1
      ;;
  esac
  printf 'https://github.com/astral-sh/uv/releases/download/%s/uv-%s.tar.gz\n' "$UV_VERSION" "$target"
}

# SHA256 of each release archive the default versions can download, checked
# before anything is unpacked. Bump these together with the default versions.
known_sha256() {
  case "$1" in
    https://github.com/typst/typst/releases/download/v0.15.1/typst-x86_64-unknown-linux-musl.tar.xz)
      echo a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c ;;
    https://github.com/typst/typst/releases/download/v0.15.1/typst-aarch64-unknown-linux-musl.tar.xz)
      echo 5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee ;;
    https://github.com/typst/typst/releases/download/v0.15.1/typst-armv7-unknown-linux-musleabi.tar.xz)
      echo 44986312e557b9ac0f2c71d5d5156c0ad93b2da374d54c859d6c0c7c0b73709f ;;
    https://github.com/typst/typst/releases/download/v0.15.1/typst-riscv64gc-unknown-linux-gnu.tar.xz)
      echo ec735f732c6a9940c4ef08223b50404396537ad34713eb883ef3bb310b396e5a ;;
    https://github.com/jgm/pandoc/releases/download/3.11/pandoc-3.11-linux-amd64.tar.gz)
      echo 37edb3bbcf722f921a009941bf5874e2e0c09263226c9b4a2d980788cb062ab6 ;;
    https://github.com/jgm/pandoc/releases/download/3.11/pandoc-3.11-linux-arm64.tar.gz)
      echo 56ed5566ec41d22ec9ee0704e6ac0b98ba102e92384efd5306173a22d314c79a ;;
    https://github.com/astral-sh/uv/releases/download/0.12.20/uv-x86_64-unknown-linux-gnu.tar.gz)
      echo 6590717592ace991ff83a63fef799e3ad9d33ecc8f96c5d6bdd732496e79337f ;;
    https://github.com/astral-sh/uv/releases/download/0.12.20/uv-aarch64-unknown-linux-gnu.tar.gz)
      echo 8a7aad7bc76a2fae5151566ff3e43eacce0b2a113d5e4de3e4afe3e58fa2441e ;;
    https://github.com/astral-sh/uv/releases/download/0.12.20/uv-armv7-unknown-linux-gnueabihf.tar.gz)
      echo e1c53318608448ad3b8e7f2e9b069936730a6e7b47831ba5446e4b4f436ee715 ;;
    https://github.com/astral-sh/uv/releases/download/0.12.20/uv-arm-unknown-linux-musleabihf.tar.gz)
      echo 6aeef64cf07b43135d6b1c20bd57809f2d965d0f8a2d5602230d5d55b8245477 ;;
    https://github.com/astral-sh/uv/releases/download/0.12.20/uv-riscv64gc-unknown-linux-gnu.tar.gz)
      echo 5cb9df36ca64fb72673c59a77b955b50f3bb907689a98d1d8d640aa3e71f2660 ;;
  esac
}

default_repo_url() {
  local script_path="${BASH_SOURCE[0]:-$0}"
  if [[ -f "$script_path" ]]; then
    local script_root
    script_root="$(cd "$(dirname "$script_path")/.." && pwd)"
    if git -c "safe.directory=$script_root" -C "$script_root" rev-parse --is-inside-work-tree &>/dev/null; then
      git -c "safe.directory=$script_root" -C "$script_root" remote get-url origin 2>/dev/null && return
    fi
  fi
  printf '%s\n' "$DEFAULT_REPO_URL"
}

REPO_URL="${REPO_URL:-$(default_repo_url)}"

need_cmd curl
need_cmd tar
need_cmd xz
need_cmd install
need_cmd sha256sum
need_cmd git
need_cmd rsync

git_app() {
  git -c "safe.directory=$APP_DIR" -C "$APP_DIR" "$@"
}

MACHINE="$(uname -m)"
TYPST_URL="${TYPST_URL:-$(default_typst_url)}"
PANDOC_URL="${PANDOC_URL:-$(default_pandoc_url)}"
UV_URL="${UV_URL:-$(default_uv_url)}"
TYPST_SHA256="${TYPST_SHA256:-$(known_sha256 "$TYPST_URL")}"
PANDOC_SHA256="${PANDOC_SHA256:-$(known_sha256 "$PANDOC_URL")}"
UV_SHA256="${UV_SHA256:-$(known_sha256 "$UV_URL")}"

echo "==> Creating layout under $INSTALL_ROOT (arch: $MACHINE)"
install -d -m 755 "$INSTALL_ROOT" "$BIN_DIR" "$DATA_DIR" "$PDF_SCRIPTS_DIR"

if ! id "$LIBMYOWN_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$INSTALL_ROOT" --shell /usr/sbin/nologin "$LIBMYOWN_USER"
fi

echo "==> Application source ($REPO_URL @ $GIT_REF)"
if [[ -d "$APP_DIR/.git" ]]; then
  chown -R "$LIBMYOWN_USER:$LIBMYOWN_USER" "$APP_DIR"
  git_app fetch origin
  git_app checkout "$GIT_REF"
  git_app pull --ff-only origin "$GIT_REF"
elif [[ -d "$APP_DIR" ]] && [[ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]]; then
  echo "$APP_DIR exists but is not a git checkout. Move it aside or remove it, then re-run." >&2
  exit 1
else
  install -d -m 755 "$(dirname "$APP_DIR")"
  git clone --branch "$GIT_REF" "$REPO_URL" "$APP_DIR"
  chown -R "$LIBMYOWN_USER:$LIBMYOWN_USER" "$APP_DIR"
fi

# An update can change this script. Finish with the new version so its install
# steps match the code that was just pulled.
if [[ -z "${LIBMYOWN_DEPLOY_REEXEC:-}" && -n "$SELF_SUM" ]] \
  && [[ "$(sha256sum < "$APP_DIR/scripts/deploy-pi.sh")" != "$SELF_SUM" ]]; then
  echo "==> The code being installed comes with a different version of this deploy script"
  if ! confirm "Continue with the new version?"; then
    echo "Stopped. The new code is checked out but not installed yet. Run the script again to finish." >&2
    exit 1
  fi
  export LIBMYOWN_DEPLOY_REEXEC=1
  exec bash "$APP_DIR/scripts/deploy-pi.sh" "$@"
fi

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

# Download a tool only when it is missing or its version (URL) changed.
install_tool() {
  local name="$1" url="$2" sha256="$3" tar_flags="$4" path_pattern="$5"
  local stamp="$BIN_DIR/.$name.source"
  if [[ -x "$BIN_DIR/$name" && -f "$stamp" && "$(cat "$stamp")" == "$url" ]]; then
    echo "==> $name is up to date"
    return
  fi
  local var="${name^^}_SHA256"
  if [[ -z "$sha256" ]]; then
    echo "Can't check the $name download from $url because this script doesn't know what it should look like." >&2
    echo "Set $var to the SHA256 listed on its release page and run again." >&2
    exit 1
  fi
  echo "==> Installing $name ($url)"
  rm -rf "${tmpdir:?}"/*
  local archive="$tmpdir/download" unpacked="$tmpdir/unpacked"
  curl -fsSL -o "$archive" "$url"
  if ! echo "${sha256,,}  $archive" | sha256sum --check --status 2>/dev/null; then
    echo "The $name download from $url doesn't match the expected checksum, so it was not installed." >&2
    echo "Try again later. If you set $var yourself, check it against the release page." >&2
    exit 1
  fi
  mkdir "$unpacked"
  tar "$tar_flags" -f "$archive" -C "$unpacked"
  local bin
  bin="$(find "$unpacked" -path "$path_pattern" -type f | head -n 1)"
  if [[ -z "$bin" ]]; then
    echo "Could not find the $name binary in $url" >&2
    exit 1
  fi
  install -m 755 "$bin" "$BIN_DIR/$name"
  printf '%s\n' "$url" >"$stamp"
}

install_tool typst "$TYPST_URL" "$TYPST_SHA256" -xJ '*/typst'
install_tool pandoc "$PANDOC_URL" "$PANDOC_SHA256" -xz '*/bin/pandoc'
install_tool uv "$UV_URL" "$UV_SHA256" -xz '*/uv'

if [[ ! -f "$APP_DIR/uv.lock" ]]; then
  echo "Checkout at $APP_DIR is missing uv.lock." >&2
  exit 1
fi

echo "==> Installing Python and dependencies"
# Python version comes from .python-version; uv downloads it into $PYTHON_DIR
# (falling back to the system python3 where no managed build exists).
(cd "$APP_DIR" && \
  UV_PROJECT_ENVIRONMENT="$VENV_DIR" \
  UV_PYTHON_INSTALL_DIR="$PYTHON_DIR" \
  UV_PYTHON_PREFERENCE=managed \
  UV_CACHE_DIR="$UV_CACHE" \
  "$BIN_DIR/uv" sync --locked --no-dev --extra pdf)

if [[ -d "$APP_DIR/pdf-scripts" ]]; then
  echo "==> Copying pdf-scripts"
  rsync -a --delete "$APP_DIR/pdf-scripts/" "$PDF_SCRIPTS_DIR/"
else
  echo "Warning: pdf-scripts not found in checkout (PDF export will be disabled)." >&2
fi

echo "==> Data directory"
install -d -m 755 "$DATA_DIR/pdf-cache"
if [[ ! -d "$DATA_DIR/stories.git" ]]; then
  git init --quiet --bare --initial-branch=main "$DATA_DIR/stories.git"
fi
if [[ ! -f "$DATA_DIR/site.json" ]]; then
  if [[ -f "$APP_DIR/data/site.json.example" ]]; then
    cp "$APP_DIR/data/site.json.example" "$DATA_DIR/site.json"
  else
    echo '{}' >"$DATA_DIR/site.json"
  fi
fi

if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "==> Creating $APP_DIR/.env from examples/env.example"
  cp "$APP_DIR/examples/env.example" "$APP_DIR/.env"
  sed -i "s|^DATA_DIR=.*|DATA_DIR=$DATA_DIR|" "$APP_DIR/.env"
  if grep -q '^# PDF_SCRIPTS=' "$APP_DIR/.env"; then
    sed -i "s|^# PDF_SCRIPTS=.*|PDF_SCRIPTS=$PDF_SCRIPTS_DIR|" "$APP_DIR/.env"
  else
    echo "PDF_SCRIPTS=$PDF_SCRIPTS_DIR" >>"$APP_DIR/.env"
  fi
  echo "Review $APP_DIR/.env (set PUBLIC_URL for your domain)." >&2
fi
chmod 600 "$APP_DIR/.env"


echo "==> Installing systemd unit"
sed "s|/opt/libmyown|$INSTALL_ROOT|g; s|^User=libmyown|User=$LIBMYOWN_USER|; s|^Group=libmyown|Group=$LIBMYOWN_USER|" \
  "$APP_DIR/examples/libmyown.service" >/etc/systemd/system/libmyown.service
systemctl daemon-reload
systemctl enable libmyown.service

chown -R "$LIBMYOWN_USER:$LIBMYOWN_USER" "$INSTALL_ROOT"

# restart pre-existing services to load the updated codebase
RESTARTED=0
if systemctl is-active --quiet libmyown.service; then
  echo "==> Restarting libmyown to load the update"
  systemctl restart libmyown.service
  RESTARTED=1
fi

echo
echo "Deploy complete."
echo "  App:         $APP_DIR ($GIT_REF)"
echo "  Data:        $DATA_DIR"
echo "  Tools:       $BIN_DIR/uv $BIN_DIR/typst $BIN_DIR/pandoc"
echo "  Environment: $APP_DIR/.env"
echo
"$BIN_DIR/typst" --version
"$BIN_DIR/pandoc" --version | head -n 1
"$BIN_DIR/uv" --version
"$VENV_DIR/bin/python" --version
echo
if [[ "$RESTARTED" -eq 1 ]]; then
  echo "The site was restarted and is running the update."
else
  echo "Next steps:"
  echo "  1. Edit $APP_DIR/.env if needed (set PUBLIC_URL, secrets are generated on first start)"
  echo "  2. sudo systemctl start libmyown"
  echo "  3. On first start the admin password is printed once to the log:"
  echo "       sudo journalctl -u libmyown | grep 'Generated admin password'"
  echo "     Log in at /login and change it under Admin -> Security"
  echo "  4. Point Caddy at 127.0.0.1:8000 (see examples/caddy/Caddyfile)"
fi
