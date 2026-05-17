#!/bin/bash
set -euo pipefail

REPO="${EYRA_REPO:-gabrimatic/eyra}"
VERSION="${EYRA_VERSION:-latest}"
INSTALL_DIR="${EYRA_INSTALL_DIR:-$HOME/.local/share/eyra/app}"
BIN_DIR="${EYRA_BIN_DIR:-$HOME/.local/bin}"
GITHUB_HOST="${GITHUB_HOST:-github.com}"
API_HOST="${GITHUB_API_HOST:-api.github.com}"
SOURCE_PATH="${EYRA_SOURCE_PATH:-}"
ALLOW_UNVERIFIED_TAG_ARCHIVE="${EYRA_ALLOW_UNVERIFIED_TAG_ARCHIVE:-false}"
VERIFY_WITH_MOCK="${EYRA_INSTALL_VERIFY_MOCK:-false}"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

log_step() { echo -e "\n${CYAN}▶${NC} $1"; }
log_ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
log_warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
log_info() { echo -e "  ${DIM}›${NC} $1"; }
fail()     { echo -e "\n  ${RED}✗${NC} $1\n"; exit 1; }
is_interactive() { [[ -r /dev/tty && -t 1 && "${EYRA_INSTALL_NO_PROMPT:-false}" != "true" ]]; }
ask_yes_no() {
    local prompt="$1"
    local default="${2:-yes}"
    local suffix="Y/n"
    [[ "$default" == "no" ]] && suffix="y/N"
    local answer
    while true; do
        read -r -p "  $prompt [$suffix]: " answer </dev/tty || return 1
        answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]')"
        if [[ -z "$answer" ]]; then
            [[ "$default" == "yes" ]]
            return
        fi
        case "$answer" in
            y|yes) return 0 ;;
            n|no) return 1 ;;
            *) log_warn "Please type yes or no." ;;
        esac
    done
}
wait_for_ollama() {
    local tries="${1:-15}"
    for _ in $(seq 1 "$tries"); do
        if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

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
    if [[ "$url" == https://api.github.com/*/releases/assets/* ]]; then
        curl -fsSL "${auth_header_args[@]}" -H "Accept: application/octet-stream" "$url" -o "$output"
    else
        curl -fsSL "${auth_header_args[@]}" "$url" -o "$output"
    fi
}

json_field() {
    local file="$1"
    local expr="$2"
    python3 - "$file" "$expr" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
expr = sys.argv[2]
if expr == "tag":
    print(payload.get("tag_name", ""))
elif expr == "wheel":
    for asset in payload.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".whl"):
            print(asset.get("url") or asset.get("browser_download_url", ""))
            break
elif expr == "wheel_name":
    for asset in payload.get("assets", []):
        name = asset.get("name", "")
        if name.endswith(".whl"):
            print(name)
            break
elif expr == "source_asset":
    for asset in payload.get("assets", []):
        name = asset.get("name", "")
        if name.endswith((".tar.gz", ".tgz", ".zip")) and "sha256" not in name.lower():
            print(asset.get("url") or asset.get("browser_download_url", ""))
            break
elif expr == "source_asset_name":
    for asset in payload.get("assets", []):
        name = asset.get("name", "")
        if name.endswith((".tar.gz", ".tgz", ".zip")) and "sha256" not in name.lower():
            print(name)
            break
elif expr == "checksum":
    for asset in payload.get("assets", []):
        name = asset.get("name", "").lower()
        if "sha256" in name or name.endswith((".sha256", ".sha256sum", ".checksums.txt", "checksums.txt")):
            print(asset.get("url") or asset.get("browser_download_url", ""))
            break
PY
}

require_safe_install_dir() {
    local target="$1"
    [[ -n "$target" ]] || fail "Install directory cannot be empty."
    [[ "$target" != "/" ]] || fail "Install directory cannot be /."
    [[ "$target" != "$HOME" ]] || fail "Install directory cannot be your home directory."
    [[ "$target" != "$HOME/.local" ]] || fail "Install directory cannot be ~/.local."
    [[ "$target" == "$HOME"/.local/share/eyra/* || "$target" == "$HOME"/Applications/* || "$target" == /opt/* ]] || \
        fail "Choose an install directory under ~/.local/share/eyra, ~/Applications, or /opt."
}

echo ""
echo -e "${BOLD}Eyra guided installer${NC}"
echo "Local-first voice coordinator for macOS."
echo -e "${DIM}It installs Eyra first, then shows clear next steps for local AI and voice.${NC}"

log_step "Checking this Mac"
[[ "$(uname -s)" == "Darwin" ]] || fail "Eyra currently supports macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Eyra currently supports Apple Silicon Macs."
log_ok "macOS Apple Silicon"

if ! command -v curl &>/dev/null; then
    fail "curl is required."
fi

log_step "Checking install location"
require_safe_install_dir "$INSTALL_DIR"
log_ok "$INSTALL_DIR"

if ! command -v brew &>/dev/null; then
    log_warn "Homebrew is not installed."
    log_info "Install it from https://brew.sh for the easiest Ollama and Local Whisper setup."
else
    log_ok "Homebrew"
fi

if python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
    log_ok "Python $(python3 --version 2>&1 | cut -d' ' -f2)"
elif command -v brew &>/dev/null; then
    log_warn "Python 3.11+ is missing. Installing Python with Homebrew."
    brew install python@3.11
else
    fail "Python 3.11+ is required. Install Python or Homebrew, then rerun the installer."
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
    if command -v brew &>/dev/null && is_interactive && ask_yes_no "Install Ollama now for the default private local model path?" "yes"; then
        log_info "Installing Ollama with Homebrew..."
        brew install --cask ollama || log_warn "Ollama install did not finish. You can install it later from https://ollama.com."
    else
        log_warn "Ollama is not installed. Install it from https://ollama.com for the default local backend."
    fi
else
    log_ok "Ollama"
fi

if ! command -v wh &>/dev/null; then
    if command -v brew &>/dev/null && is_interactive && ask_yes_no "Install Local Whisper now for speech and microphone support?" "yes"; then
        log_info "Installing Local Whisper..."
        brew tap gabrimatic/local-whisper 2>/dev/null || true
        brew install gabrimatic/local-whisper/local-whisper || log_warn "Local Whisper install did not finish. Voice can be repaired later with: brew tap gabrimatic/local-whisper && brew install local-whisper"
    elif command -v brew &>/dev/null; then
        log_warn "Local Whisper is missing. Install later with: brew tap gabrimatic/local-whisper && brew install local-whisper"
    else
        log_warn "Local Whisper is missing. Voice will stay unavailable until it is installed."
    fi
else
    log_ok "Local Whisper"
fi

if command -v ollama &>/dev/null && ! curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
    log_info "Starting Ollama..."
    open -a Ollama >/dev/null 2>&1 || true
    if ! wait_for_ollama 8; then
        (ollama serve >/dev/null 2>&1 &)
        wait_for_ollama 10 || log_warn "Ollama is installed but not running. Open the Ollama app, then run: eyra setup"
    fi
fi

if command -v wh &>/dev/null && ! wh status 2>&1 | grep -qi running; then
    log_info "Starting Local Whisper..."
    wh start 2>/dev/null || brew services start gabrimatic/local-whisper/local-whisper 2>/dev/null || true
fi

tmp_dir="$(mktemp -d)"
backup=""
package_kind=""
package_path=""
asset_name=""

log_step "Resolving Eyra package"
if [[ -n "$SOURCE_PATH" ]]; then
    [[ -d "$SOURCE_PATH" ]] || fail "EYRA_SOURCE_PATH does not exist: $SOURCE_PATH"
    package_kind="source-dir"
    package_path="$SOURCE_PATH"
    tag="local-source"
    log_ok "Using local source path"
elif [[ "$VERSION" == "latest" || "$VERSION" == v* ]]; then
    release_json="$tmp_dir/release.json"
    release_api="https://${API_HOST}/repos/${REPO}/releases/latest"
    if [[ "$VERSION" != "latest" ]]; then
        release_api="https://${API_HOST}/repos/${REPO}/releases/tags/${VERSION}"
    fi
    if download "$release_api" "$release_json"; then
        tag="$(json_field "$release_json" tag)"
        wheel_url="$(json_field "$release_json" wheel)"
        source_asset_url="$(json_field "$release_json" source_asset)"
        checksum_url="$(json_field "$release_json" checksum)"
        if [[ -n "$wheel_url" ]]; then
            package_kind="wheel"
            asset_name="$(json_field "$release_json" wheel_name)"
            package_path="$tmp_dir/$asset_name"
            download "$wheel_url" "$package_path"
        elif [[ -n "$source_asset_url" ]]; then
            package_kind="archive"
            asset_name="$(json_field "$release_json" source_asset_name)"
            download "$source_asset_url" "$tmp_dir/$asset_name"
            package_path="$tmp_dir/$asset_name"
        else
            log_warn "Release ${tag} has no wheel or source archive asset."
        fi
        if [[ -n "${checksum_url:-}" && -n "$package_path" ]]; then
            checksum_file="$tmp_dir/checksums.txt"
            download "$checksum_url" "$checksum_file"
            if grep -F "  $asset_name" "$checksum_file" >/dev/null 2>&1; then
                (cd "$tmp_dir" && shasum -a 256 -c "$checksum_file" --ignore-missing)
            else
                expected="$(awk '{print $1; exit}' "$checksum_file")"
                actual="$(shasum -a 256 "$package_path" | awk '{print $1}')"
                [[ "$expected" == "$actual" ]] || fail "Checksum mismatch for release asset."
            fi
            log_ok "Checksum verified"
        elif [[ -n "$package_path" ]]; then
            fail "Release asset checksum is missing. Add a checksum release asset or set EYRA_ALLOW_UNVERIFIED_TAG_ARCHIVE=true for tag archive fallback only."
        fi
    else
        [[ "$VERSION" != "latest" ]] || fail "Could not read the latest GitHub release. If the repo is private, set GITHUB_TOKEN or install from a checked-out source tree."
        log_warn "Could not read release JSON for ${VERSION}; falling back to tag archive."
    fi
else
    tag="$VERSION"
fi

if [[ -z "$package_kind" ]]; then
    archive_url="https://${GITHUB_HOST}/${REPO}/archive/refs/tags/${tag}.tar.gz"
    if [[ "$ALLOW_UNVERIFIED_TAG_ARCHIVE" != "true" ]]; then
        fail "No release asset checksum is available for ${tag}. Set EYRA_ALLOW_UNVERIFIED_TAG_ARCHIVE=true to install the GitHub tag archive with an explicit warning."
    fi
    log_warn "Installing GitHub tag archive without a release asset checksum because EYRA_ALLOW_UNVERIFIED_TAG_ARCHIVE=true."
    package_kind="archive"
    package_path="$tmp_dir/eyra.tar.gz"
    if ! download "$archive_url" "$package_path"; then
        fail "Could not download ${archive_url}. Private repositories require GITHUB_TOKEN."
    fi
fi

log_step "Installing into ${INSTALL_DIR}"
staging="$tmp_dir/staging"
mkdir -p "$staging"
case "$package_kind" in
    source-dir)
        cp -R "$package_path"/. "$staging"/
        rm -rf "$staging/.git" "$staging/.venv" "$staging/.pytest_cache" "$staging/dist" "$staging/build"
        ;;
    archive)
        case "$package_path" in
            *.zip)
                unzip -q "$package_path" -d "$tmp_dir/unpacked"
                first_entry="$(find "$tmp_dir/unpacked" -mindepth 1 -maxdepth 1 | head -n 1)"
                [[ -n "$first_entry" ]] || fail "Downloaded archive is empty."
                cp -R "$first_entry"/. "$staging"/
                ;;
            *)
                tar -xzf "$package_path" -C "$staging" --strip-components 1
                ;;
        esac
        ;;
    wheel)
        mkdir -p "$staging"
        ;;
    *)
        fail "Unsupported package kind: $package_kind"
        ;;
esac
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -e "$INSTALL_DIR" ]]; then
    backup="${INSTALL_DIR}.backup.$(date +%Y%m%d%H%M%S)"
    mv "$INSTALL_DIR" "$backup"
    log_warn "Existing install moved to $backup"
fi
mv "$staging" "$INSTALL_DIR"

rollback_install() {
    log_warn "Install verification failed; rolling back install directory."
    rm -rf "$INSTALL_DIR"
    [[ -n "${backup:-}" && -d "$backup" ]] && mv "$backup" "$INSTALL_DIR"
}

if [[ "$package_kind" == "wheel" ]]; then
    if ! uv venv "$INSTALL_DIR/.venv" >/dev/null; then
        rollback_install
        fail "Could not create the Eyra package environment."
    fi
    if ! uv pip install --python "$INSTALL_DIR/.venv/bin/python" "$package_path"; then
        rollback_install
        fail "Could not install the Eyra wheel."
    fi
fi

write_shim() {
    local name="$1"
    shift
    cat > "$BIN_DIR/$name" <<LAUNCHER
#!/bin/bash
if [[ -x "$INSTALL_DIR/.venv/bin/eyra" ]]; then
    exec "$INSTALL_DIR/.venv/bin/eyra" "$@"
fi
cd "$INSTALL_DIR" && exec uv run --frozen eyra "$@"
LAUNCHER
    chmod +x "$BIN_DIR/$name"
}

mkdir -p "$BIN_DIR"
write_shim eyra "\$@"
cat > "$BIN_DIR/eyra-web" <<LAUNCHER
#!/bin/bash
exec "$BIN_DIR/eyra" web "\$@"
LAUNCHER
chmod +x "$BIN_DIR/eyra-web"
for name in eyra-doctor eyra-certify eyra-setup eyra-connectors eyra-menu; do
    subcommand="${name#eyra-}"
    cat > "$BIN_DIR/$name" <<LAUNCHER
#!/bin/bash
exec "$BIN_DIR/eyra" "$subcommand" "\$@"
LAUNCHER
    chmod +x "$BIN_DIR/$name"
done

log_step "Running first-run setup"
if is_interactive; then
    if ! "$BIN_DIR/eyra" setup </dev/tty; then
        rollback_install
        exit 1
    fi
else
    if ! "$BIN_DIR/eyra" setup --non-interactive; then
        rollback_install
        exit 1
    fi
fi

log_step "Verifying installed commands"
verify_env=(USE_MOCK_CLIENT=false LIVE_LISTENING_ENABLED=false LIVE_SPEECH_ENABLED=false)
if [[ "$VERIFY_WITH_MOCK" == "true" ]]; then
    log_warn "Installer verification is using the mock client because EYRA_INSTALL_VERIFY_MOCK=true."
    verify_env=(USE_MOCK_CLIENT=true LIVE_LISTENING_ENABLED=false LIVE_SPEECH_ENABLED=false)
fi
if ! (
    "$BIN_DIR/eyra" version >/dev/null
    "$BIN_DIR/eyra" paths --json >/dev/null
    "$BIN_DIR/eyra" setup --non-interactive --json >/dev/null
); then
    rollback_install
    exit 1
fi
log_ok "Installed command shims in $BIN_DIR"

if env "${verify_env[@]}" "$BIN_DIR/eyra" doctor --json >/dev/null; then
    log_ok "Runtime doctor passed"
else
    log_warn "Runtime doctor needs attention. Run: eyra doctor"
fi

if [[ "${EYRA_INSTALL_STRICT_VERIFY:-false}" == "true" ]]; then
    if ! env "${verify_env[@]}" "$BIN_DIR/eyra" certify --json >/dev/null; then
        rollback_install
        fail "Strict install verification failed during certification."
    fi
elif env "${verify_env[@]}" "$BIN_DIR/eyra" certify --json >/dev/null; then
    log_ok "Certification passed"
else
    log_warn "Certification needs attention. Run: eyra certify after local AI and voice are ready."
fi

echo ""
echo -e "${GREEN}${BOLD}Eyra installed${NC}"
echo "Start: eyra"
echo "Web controls: eyra open"
echo "Menu bar preview check: eyra menu --json"
echo "Useful first prompts: eyra examples"
echo "Setup or repair: eyra setup"
echo "Support report: eyra doctor"
echo "Update: eyra update"
echo "Uninstall shims: eyra uninstall"
echo -e "${DIM}If doctor says something needs attention, it is the next setup item to finish, not a failed install.${NC}"
echo "User config and data stay in ~/.config/eyra, ~/.local/share/eyra, and ~/Library/Logs/Eyra."
