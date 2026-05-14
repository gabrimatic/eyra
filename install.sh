#!/bin/bash
set -euo pipefail

REPO="${EYRA_REPO:-gabrimatic/eyra}"
VERSION="${EYRA_VERSION:-latest}"
INSTALL_DIR="${EYRA_INSTALL_DIR:-$HOME/.local/share/eyra/app}"
BIN_DIR="${EYRA_BIN_DIR:-$HOME/.local/bin}"
GITHUB_HOST="${GITHUB_HOST:-github.com}"
API_HOST="${GITHUB_API_HOST:-api.github.com}"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

log_step() { echo -e "\n${CYAN}▶${NC} $1"; }
log_ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
log_warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
fail()     { echo -e "\n  ${RED}✗${NC} $1\n"; exit 1; }

tmp_dir=""
cleanup() {
    [[ -n "$tmp_dir" && -d "$tmp_dir" ]] && rm -rf "$tmp_dir"
}
trap cleanup EXIT

auth_header_args=()
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    auth_header_args=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
fi

download() {
    local url="$1"
    local output="$2"
    curl -fsSL "${auth_header_args[@]}" "$url" -o "$output"
}

echo ""
echo -e "${BOLD}Eyra installer${NC}"
echo "Local-first voice coordinator for macOS."

log_step "Checking this Mac"
[[ "$(uname -s)" == "Darwin" ]] || fail "Eyra currently supports macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Eyra currently supports Apple Silicon Macs."
log_ok "macOS Apple Silicon"

if ! command -v curl &>/dev/null; then
    fail "curl is required."
fi

if ! command -v brew &>/dev/null; then
    log_warn "Homebrew is not installed. Install it from https://brew.sh for Ollama and Local Whisper guidance."
else
    log_ok "Homebrew"
fi

if ! command -v uv &>/dev/null; then
    log_warn "uv is missing. Installing uv in your user account."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv &>/dev/null || fail "uv is still not on PATH. Open a new terminal and rerun the installer."
log_ok "uv"

if ! command -v ollama &>/dev/null; then
    log_warn "Ollama is not installed. Install it from https://ollama.com for the default local backend."
else
    log_ok "Ollama"
fi

if ! command -v wh &>/dev/null; then
    if command -v brew &>/dev/null; then
        log_warn "Local Whisper is missing. Install later with: brew tap gabrimatic/local-whisper && brew install local-whisper"
    else
        log_warn "Local Whisper is missing. Voice will stay unavailable until it is installed."
    fi
else
    log_ok "Local Whisper"
fi

tmp_dir="$(mktemp -d)"
archive="$tmp_dir/eyra.tar.gz"

log_step "Downloading Eyra"
if [[ "$VERSION" == "latest" ]]; then
    release_json="$tmp_dir/release.json"
    if ! download "https://${API_HOST}/repos/${REPO}/releases/latest" "$release_json"; then
        fail "Could not read the latest GitHub release. If the repo is private, set GITHUB_TOKEN or install from a checked-out source tree."
    fi
    tag="$(python3 - "$release_json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["tag_name"])
PY
)"
else
    tag="$VERSION"
fi

archive_url="https://${GITHUB_HOST}/${REPO}/archive/refs/tags/${tag}.tar.gz"
if ! download "$archive_url" "$archive"; then
    fail "Could not download ${archive_url}. Private repositories require GITHUB_TOKEN."
fi

checksum_url="${archive_url}.sha256"
checksum_file="$tmp_dir/eyra.tar.gz.sha256"
if download "$checksum_url" "$checksum_file" 2>/dev/null; then
    (cd "$tmp_dir" && shasum -a 256 -c "$checksum_file")
    log_ok "Checksum verified"
else
    log_warn "No checksum file found for ${tag}; continuing with HTTPS transport only."
fi

log_step "Installing into ${INSTALL_DIR}"
staging="$tmp_dir/staging"
mkdir -p "$staging"
tar -xzf "$archive" -C "$staging" --strip-components 1
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -e "$INSTALL_DIR" ]]; then
    backup="${INSTALL_DIR}.backup.$(date +%Y%m%d%H%M%S)"
    mv "$INSTALL_DIR" "$backup"
    log_warn "Existing install moved to $backup"
fi
mv "$staging" "$INSTALL_DIR"

log_step "Running first-run setup"
chmod +x "$INSTALL_DIR/setup.sh"
if ! "$INSTALL_DIR/setup.sh" --non-interactive; then
    log_warn "Setup failed; rolling back install directory."
    rm -rf "$INSTALL_DIR"
    [[ -n "${backup:-}" && -d "$backup" ]] && mv "$backup" "$INSTALL_DIR"
    exit 1
fi

mkdir -p "$BIN_DIR"
log_ok "Installed command shims in $BIN_DIR"

echo ""
echo -e "${GREEN}${BOLD}Eyra installed${NC}"
echo "Start: eyra"
echo "Support report: eyra doctor --json"
echo "Update: eyra update"
echo "Uninstall shims: eyra uninstall"
