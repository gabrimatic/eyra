#!/bin/bash
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

NON_INTERACTIVE=false
for arg in "$@"; do
    case "$arg" in
        --non-interactive) NON_INTERACTIVE=true ;;
        -h|--help)
            echo "Usage: ./setup.sh [--non-interactive]"
            exit 0
            ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done
START_SERVICES="${EYRA_SETUP_START_SERVICES:-true}"
if "$NON_INTERACTIVE"; then
    START_SERVICES=false
fi

log_step() { echo -e "\n${CYAN}▶${NC} $1"; }
log_ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
log_warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
log_info() { echo -e "  ${DIM}›${NC} $1"; }
fail()     { echo -e "\n  ${RED}✗${NC} $1\n"; exit 1; }
is_interactive() { [[ "$NON_INTERACTIVE" == "false" && -r /dev/tty && -t 1 ]]; }
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

trap 'echo ""; echo -e "  ${DIM}Setup cancelled.${NC}"; echo ""; exit 130' INT

EYRA_DIR="$(cd "$(dirname "$0")" && pwd)"
command -v realpath &>/dev/null && EYRA_DIR="$(realpath "$EYRA_DIR")"
BIN_DIR="$HOME/.local/bin"

echo ""
echo -e "${BOLD}╭────────────────────────────────────────╮${NC}"
echo -e "${BOLD}│${NC}  ${CYAN}Eyra${NC} · First-run setup               ${BOLD}│${NC}"
echo -e "${BOLD}│${NC}  ${DIM}Local-first voice coordinator${NC}          ${BOLD}│${NC}"
echo -e "${BOLD}╰────────────────────────────────────────╯${NC}"

log_step "Checking this Mac"
[[ "$(uname -s)" == "Darwin" ]] || fail "Eyra currently supports macOS."
[[ "$(uname -m)" == "arm64" ]] || fail "Eyra currently supports Apple Silicon Macs."
log_ok "macOS $(sw_vers -productVersion) on Apple Silicon"

if command -v brew &>/dev/null; then
    log_ok "Homebrew"
elif is_interactive && ask_yes_no "Install Homebrew now? macOS may ask for your password." "yes"; then
    log_info "Opening the official Homebrew installer..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || fail "Homebrew install did not finish."
    if [[ -x /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    command -v brew &>/dev/null || fail "Homebrew installed, but it is not on PATH yet. Open a new terminal and rerun setup."
    log_ok "Homebrew"
else
    log_warn "Homebrew is not installed."
    log_info "Install it from https://brew.sh, then rerun setup for the easiest local AI and voice setup."
fi

if python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
    log_ok "Python $(python3 --version 2>&1 | cut -d' ' -f2)"
elif command -v brew &>/dev/null; then
    log_info "Installing Python 3.11+ with Homebrew..."
    brew install python@3.11 || fail "Failed to install Python 3.11+."
else
    fail "Python 3.11+ is required. Install Python or Homebrew, then rerun setup."
fi

if command -v uv &>/dev/null; then
    log_ok "uv $(uv --version | awk '{print $2}')"
else
    log_info "Installing uv in your user account..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
    command -v uv &>/dev/null || fail "uv installation finished but uv is not on PATH. Open a new terminal and rerun setup."
    log_ok "uv"
fi

log_step "Installing Eyra dependencies"
cd "$EYRA_DIR"
uv sync -q
log_ok "Python environment ready"

log_step "Checking default local model provider"
if command -v ollama &>/dev/null; then
    log_ok "Ollama command found"
elif command -v brew &>/dev/null && is_interactive && ask_yes_no "Install Ollama now for the default private local model path?" "yes"; then
    log_info "Installing Ollama with Homebrew..."
    brew install --cask ollama || fail "Ollama install did not finish."
    log_ok "Ollama installed"
else
    log_warn "Ollama is not installed."
    log_info "Install it from https://ollama.com if you want the default local backend."
fi

if command -v ollama &>/dev/null; then
    if curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1; then
        log_ok "Ollama is running"
    elif [[ "$START_SERVICES" == "true" ]]; then
        log_info "Starting Ollama..."
        open -a Ollama >/dev/null 2>&1 || true
        if ! wait_for_ollama 8; then
            (ollama serve >/dev/null 2>&1 &)
            wait_for_ollama 10 || log_warn "Ollama is installed but not running. Open the Ollama app, then rerun: eyra setup"
        fi
        curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && log_ok "Ollama is running"
    else
        log_warn "Ollama is installed but not running. Open the Ollama app when you are ready to use the local model."
    fi
fi

log_step "Checking local voice dependencies"
if command -v wh &>/dev/null; then
    log_ok "Local Whisper command found"
elif command -v brew &>/dev/null && { ! is_interactive || ask_yes_no "Install Local Whisper now for speech and microphone support?" "yes"; }; then
    log_info "Installing Local Whisper from the existing tap..."
    brew tap gabrimatic/local-whisper 2>/dev/null || true
    brew install gabrimatic/local-whisper/local-whisper || log_warn "Local Whisper install did not complete. Voice can be repaired later with: brew tap gabrimatic/local-whisper && brew install local-whisper"
else
    log_warn "Local Whisper is not installed. Voice input/output will stay unavailable until you install it."
fi

if command -v wh &>/dev/null; then
    if wh status 2>&1 | grep -qi running; then
        log_ok "Local Whisper is running"
    elif [[ "$START_SERVICES" == "true" ]]; then
        log_info "Starting Local Whisper..."
        wh start 2>/dev/null || brew services start gabrimatic/local-whisper/local-whisper 2>/dev/null || true
        if wh status 2>&1 | grep -qi running; then
            log_ok "Local Whisper is running"
        else
            log_warn "Local Whisper is installed but not running. Start it later with: wh start"
        fi
    else
        log_warn "Local Whisper is installed but not running. Start it later with: wh start"
    fi
fi

log_step "Preparing local configuration"
if "$NON_INTERACTIVE"; then
    uv run eyra setup --non-interactive
else
    uv run eyra setup </dev/tty
fi

log_step "Registering commands"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/eyra" <<LAUNCHER
#!/bin/bash
cd "$EYRA_DIR" && exec uv run eyra "\$@"
LAUNCHER
chmod +x "$BIN_DIR/eyra"

cat > "$BIN_DIR/eyra-web" <<LAUNCHER
#!/bin/bash
cd "$EYRA_DIR" && exec uv run eyra web "\$@"
LAUNCHER
chmod +x "$BIN_DIR/eyra-web"

for name in eyra-doctor eyra-certify eyra-setup eyra-connectors eyra-menu; do
    subcommand="${name#eyra-}"
    cat > "$BIN_DIR/$name" <<LAUNCHER
#!/bin/bash
cd "$EYRA_DIR" && exec uv run eyra "$subcommand" "\$@"
LAUNCHER
    chmod +x "$BIN_DIR/$name"
done

PATH_LINE='export PATH="$HOME/.local/bin:$PATH" # eyra'
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    [[ -f "$rc" ]] || continue
    sed -i '' '/# eyra$/d' "$rc"
    echo "$PATH_LINE" >> "$rc"
done
export PATH="$BIN_DIR:$PATH"
log_ok "Commands registered in $BIN_DIR"

log_step "Running doctor"
if uv run eyra doctor; then
    log_ok "Doctor passed"
else
    log_warn "Doctor found something to fix. The report above tells you what is missing."
fi

echo ""
echo -e "${GREEN}${BOLD}Setup complete${NC}"
echo ""
echo -e "  Start Eyra: ${BOLD}eyra${NC}"
echo -e "  Open menu bar controls: ${BOLD}eyra menu${NC}"
echo -e "  See useful first prompts: ${BOLD}eyra examples${NC}"
echo -e "  Open the Web UI: ${BOLD}eyra open${NC}"
echo -e "  Check support diagnostics: ${BOLD}eyra doctor${NC}"
echo -e "  Run certification: ${BOLD}eyra certify${NC}"
echo ""
echo -e "  ${DIM}If doctor says something needs attention, nothing is broken; it is the next setup item to finish.${NC}"
echo -e "  ${DIM}Eyra preserved existing .env, jobs, triggers, logs, and local data.${NC}"
