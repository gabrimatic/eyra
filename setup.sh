#!/bin/bash
set -e

# ── Style ─────────────────────────────────────────────────────────────────────

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

trap 'echo ""; echo -e "  ${DIM}Setup cancelled.${NC}"; echo ""; exit 130' INT

# ── Header ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}╭────────────────────────────────────────╮${NC}"
echo -e "${BOLD}│${NC}  ${CYAN}Eyra${NC} · Setup                         ${BOLD}│${NC}"
echo -e "${BOLD}│${NC}  ${DIM}Voice-first AI assistant${NC}               ${BOLD}│${NC}"
echo -e "${BOLD}╰────────────────────────────────────────╯${NC}"

# ── System requirements ───────────────────────────────────────────────────────

log_step "Checking system requirements..."

[[ "$(uname -s)" == "Darwin" ]] || fail "macOS required."
[[ "$(uname -m)" == "arm64" ]] || fail "Apple Silicon required."
log_ok "macOS $(sw_vers -productVersion) (Apple Silicon)"

if ! command -v brew &>/dev/null; then
    fail "Homebrew required. Install: https://brew.sh"
fi
log_ok "Homebrew"

if ! python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
    log_info "Installing Python 3.11+..."
    brew install python@3.11 || fail "Failed to install Python"
fi
log_ok "Python $(python3 --version 2>&1 | cut -d' ' -f2)"

if ! command -v uv &>/dev/null; then
    log_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
    command -v uv &>/dev/null || fail "uv installation failed. Restart your terminal and try again."
fi
log_ok "uv"

# ── Python dependencies ───────────────────────────────────────────────────────

log_step "Installing Python dependencies..."
uv sync -q
log_ok "Dependencies installed"

# ── Permissions ───────────────────────────────────────────────────────────────

log_step "Checking permissions..."

if command -v screencapture &>/dev/null; then
    if screencapture -x /tmp/eyra_setup_test.png 2>/dev/null; then
        rm -f /tmp/eyra_setup_test.png
        log_ok "Screen capture"
    else
        log_warn "Screen capture: grant permission in System Settings > Privacy > Screen Recording"
    fi
fi

# Local Whisper (voice input + speech)
if ! command -v wh &>/dev/null; then
    log_info "Installing Local Whisper..."
    brew tap gabrimatic/local-whisper 2>/dev/null
    brew install gabrimatic/local-whisper/local-whisper
fi

if command -v wh &>/dev/null; then
    log_ok "Local Whisper (installed)"
    if wh status 2>&1 | grep -qi running; then
        log_ok "Local Whisper: running (voice input + speech)"
    else
        log_info "Starting Local Whisper..."
        wh start 2>/dev/null || brew services start gabrimatic/local-whisper/local-whisper 2>/dev/null
        sleep 1
        if wh status 2>&1 | grep -qi running; then
            log_ok "Local Whisper: running (voice input + speech)"
        else
            log_warn "Local Whisper: installed but not running"
            log_info "Start: wh start"
        fi
    fi
else
    log_warn "Local Whisper: not installed (voice input + speech disabled)"
    log_info "Install: brew tap gabrimatic/local-whisper && brew install local-whisper"
fi

# ── Register command ──────────────────────────────────────────────────────────

EYRA_DIR="$(cd "$(dirname "$0")" && pwd)"
command -v realpath &>/dev/null && EYRA_DIR="$(realpath "$EYRA_DIR")"

log_step "Registering eyra command..."

BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/eyra" <<LAUNCHER
#!/bin/bash
cd "$EYRA_DIR" && exec uv run python src/main.py "\$@"
LAUNCHER
chmod +x "$BIN_DIR/eyra"

EYRA_PATH_LINE='export PATH="$HOME/.local/bin:$PATH" # eyra'
EYRA_ALIAS="alias eyra='$BIN_DIR/eyra' # eyra"
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    [[ -f "$rc" ]] || continue
    sed -i '' '/# eyra$/d' "$rc"
    if ! grep -qF '.local/bin' "$rc"; then
        echo "$EYRA_PATH_LINE" >> "$rc"
    fi
    echo "$EYRA_ALIAS" >> "$rc"
done

export PATH="$BIN_DIR:$PATH"
log_ok "eyra command registered"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}  Setup complete!${NC}"
echo ""
echo -e "  ${BOLD}Run:${NC} eyra"
echo -e "  ${DIM}You'll choose your AI provider on first launch.${NC}"
echo ""
