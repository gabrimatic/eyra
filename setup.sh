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

# ── Header ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}╭────────────────────────────────────────╮${NC}"
echo -e "${BOLD}│${NC}  ${CYAN}Eyra${NC} · Setup                         ${BOLD}│${NC}"
echo -e "${BOLD}│${NC}  ${DIM}Real-time AI screen analysis${NC}          ${BOLD}│${NC}"
echo -e "${BOLD}╰────────────────────────────────────────╯${NC}"

# ── System requirements ───────────────────────────────────────────────────────

log_step "Checking system requirements..."

[[ "$(uname -s)" == "Darwin" ]] || fail "macOS required."
[[ "$(uname -m)" == "arm64" ]] || fail "Apple Silicon required."
log_ok "macOS $(sw_vers -productVersion) (Apple Silicon)"

# ── Homebrew ──────────────────────────────────────────────────────────────────

if ! command -v brew &>/dev/null; then
    fail "Homebrew required. Install: https://brew.sh"
fi
log_ok "Homebrew"

# ── Python ────────────────────────────────────────────────────────────────────

if ! python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
    log_info "Installing Python 3.11+..."
    brew install python@3.11 || fail "Failed to install Python"
fi
log_ok "Python $(python3 --version 2>&1 | cut -d' ' -f2)"

# ── uv ────────────────────────────────────────────────────────────────────────

if ! command -v uv &>/dev/null; then
    log_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
fi
log_ok "uv"

# ── Python dependencies ──────────────────────────────────────────────────────

log_step "Installing Python dependencies..."
uv sync -q
log_ok "Dependencies installed"

# ── Configuration ─────────────────────────────────────────────────────────────

log_step "Configuring..."

if [[ ! -f .env ]]; then
    cp .env.example .env
    log_ok "Created .env from .env.example"
else
    # Sync any missing keys from .env.example into .env
    ADDED=0
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        if ! grep -q "^${key}=" .env; then
            echo "${key}=${value}" >> .env
            ADDED=$((ADDED + 1))
        fi
    done < .env.example
    if [[ $ADDED -gt 0 ]]; then
        log_ok "Added $ADDED new keys to .env"
    else
        log_ok ".env up to date"
    fi
fi

# ── Backend ───────────────────────────────────────────────────────────────────

API_BASE=$(grep '^API_BASE_URL=' .env | sed 's/.*=//' | sed 's|/v1$||')
IS_OLLAMA=false

log_step "Checking backend..."

# Detect if backend is already reachable
if curl -sf "${API_BASE}/v1/models" &>/dev/null; then
    log_ok "Backend reachable: ${API_BASE}"
elif curl -sf "${API_BASE}/api/tags" &>/dev/null; then
    IS_OLLAMA=true
    log_ok "Backend reachable: ${API_BASE} (Ollama)"
else
    # Not reachable. If it looks like default Ollama URL, try to set it up.
    if [[ "$API_BASE" == *"localhost:11434"* ]] || [[ "$API_BASE" == *"127.0.0.1:11434"* ]]; then
        IS_OLLAMA=true
        if ! command -v ollama &>/dev/null; then
            log_info "Installing Ollama..."
            brew install ollama || fail "Failed to install Ollama"
        fi
        log_info "Starting Ollama..."
        ollama serve &>/dev/null &
        for i in {1..10}; do
            curl -sf "${API_BASE}/api/tags" &>/dev/null && break
            sleep 1
        done
        curl -sf "${API_BASE}/api/tags" &>/dev/null || fail "Backend failed to start"
        log_ok "Backend started: ${API_BASE} (Ollama)"
    else
        log_warn "Backend unreachable: ${API_BASE}"
        log_info "Start your backend, then run setup again."
        log_info "Skipping model checks."
    fi
fi

# ── Models ────────────────────────────────────────────────────────────────────

MODELS=$(grep '_MODEL=' .env | sed 's/.*=//' | sort -u)

if [[ "$IS_OLLAMA" == "true" ]] && command -v ollama &>/dev/null; then
    log_step "Checking models..."
    for model in $MODELS; do
        if ollama list 2>/dev/null | grep -q "^${model}[[:space:]]"; then
            log_ok "$model"
        else
            log_info "Pulling $model..."
            if ollama pull "$model" >/dev/null 2>&1; then
                log_ok "$model"
            else
                log_warn "Failed to pull $model (run later: ollama pull $model)"
            fi
        fi
    done
elif curl -sf "${API_BASE}/v1/models" &>/dev/null; then
    log_step "Checking models..."
    AVAILABLE=$(curl -sf "${API_BASE}/v1/models" | python3 -c "import sys,json; print(' '.join(m['id'] for m in json.load(sys.stdin).get('data',[])))" 2>/dev/null || echo "")
    for model in $MODELS; do
        if echo "$AVAILABLE" | grep -qw "$model"; then
            log_ok "$model"
        else
            log_warn "Model not found: $model"
        fi
    done
fi

# ── Permissions ───────────────────────────────────────────────────────────────

log_step "Checking permissions..."

# Screen recording: trigger the permission dialog by attempting a capture
if command -v screencapture &>/dev/null; then
    screencapture -x /tmp/eyra_setup_test.png 2>/dev/null && rm -f /tmp/eyra_setup_test.png
    log_ok "Screen capture permission requested"
fi

# Microphone: check that wh service is running (mic permission is handled by Local Whisper)
if command -v wh &>/dev/null; then
    if wh status &>/dev/null; then
        log_ok "Local Whisper: running (voice + mic)"
    else
        log_warn "Local Whisper: not running (start with: wh start)"
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}  ✓ Setup complete!${NC}"
echo ""
echo -e "  ${BOLD}Run:${NC} uv run python src/main.py"
echo ""
