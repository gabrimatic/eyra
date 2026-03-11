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
echo -e "${BOLD}│${NC}  ${DIM}Voice-first AI assistant${NC}               ${BOLD}│${NC}"
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

# ── Provider selection ────────────────────────────────────────────────────────

log_step "Choose your AI provider"
echo ""
echo -e "  ${BOLD}1${NC}  Ollama          ${DIM}Local, free, runs on your Mac${NC}"
echo -e "  ${BOLD}2${NC}  LM Studio       ${DIM}Local, free, GUI model manager${NC}"
echo -e "  ${BOLD}3${NC}  OpenRouter      ${DIM}Cloud, free tier, 100+ models${NC}"
echo -e "  ${BOLD}4${NC}  Groq            ${DIM}Cloud, free tier, fast inference${NC}"
echo -e "  ${BOLD}5${NC}  OpenAI          ${DIM}Cloud, paid, reference quality${NC}"
echo -e "  ${BOLD}6${NC}  Custom          ${DIM}Any OpenAI-compatible endpoint${NC}"
echo ""

# If .env already exists with a configured provider, offer to keep it
if [[ -f .env ]] && grep -q '^API_BASE_URL=' .env; then
    EXISTING_URL=$(grep '^API_BASE_URL=' .env | sed 's/^API_BASE_URL=//')
    EXISTING_MODEL=$(grep '^MODEL=' .env | sed 's/^MODEL=//')
    echo -e "  ${DIM}Current: ${EXISTING_URL} (${EXISTING_MODEL})${NC}"
    echo -e "  ${DIM}Press Enter to keep current config, or choose a number.${NC}"
    echo ""
fi

read -rp "  Provider [1-6]: " PROVIDER_CHOICE

# Default: keep existing if .env exists, otherwise Ollama
if [[ -z "$PROVIDER_CHOICE" ]]; then
    if [[ -f .env ]] && grep -q '^API_BASE_URL=' .env; then
        log_ok "Keeping current configuration"
        SKIP_PROVIDER_CONFIG=true
    else
        PROVIDER_CHOICE=1
    fi
fi

API_BASE_URL=""
API_KEY=""
MODEL=""
IS_OLLAMA=false
SKIP_PROVIDER_CONFIG=${SKIP_PROVIDER_CONFIG:-false}

if [[ "$SKIP_PROVIDER_CONFIG" == "false" ]]; then
    case "$PROVIDER_CHOICE" in
        1)
            # ── Ollama ────────────────────────────────────────────────────
            IS_OLLAMA=true
            API_BASE_URL="http://localhost:11434/v1"
            API_KEY="ollama"

            if ! command -v ollama &>/dev/null; then
                log_info "Installing Ollama..."
                brew install ollama || fail "Failed to install Ollama"
            fi
            log_ok "Ollama installed"

            # Start Ollama if not running
            if ! curl -sf "http://localhost:11434/api/tags" &>/dev/null; then
                log_info "Starting Ollama..."
                ollama serve &>/dev/null &
                for i in {1..10}; do
                    curl -sf "http://localhost:11434/api/tags" &>/dev/null && break
                    sleep 1
                done
                curl -sf "http://localhost:11434/api/tags" &>/dev/null || fail "Ollama failed to start"
            fi
            log_ok "Ollama running"

            # Model selection
            echo ""
            echo -e "  ${BOLD}Choose a model${NC} ${DIM}(must support tool calling)${NC}"
            echo ""
            echo -e "  ${BOLD}1${NC}  qwen3.5:4b      ${DIM}Fast, 4B params, good tool use${NC}"
            echo -e "  ${BOLD}2${NC}  qwen3.5:8b      ${DIM}Balanced, 8B params${NC}"
            echo -e "  ${BOLD}3${NC}  llama3.2:3b     ${DIM}Fast, 3B params, Meta${NC}"
            echo -e "  ${BOLD}4${NC}  mistral:7b      ${DIM}Balanced, 7B params${NC}"
            echo -e "  ${BOLD}5${NC}  Custom          ${DIM}Enter model name manually${NC}"
            echo ""
            read -rp "  Model [1-5]: " MODEL_CHOICE

            case "${MODEL_CHOICE:-1}" in
                1) MODEL="qwen3.5:4b" ;;
                2) MODEL="qwen3.5:8b" ;;
                3) MODEL="llama3.2:3b" ;;
                4) MODEL="mistral:7b" ;;
                5)
                    read -rp "  Model name (e.g. qwen3.5:4b): " MODEL
                    [[ -z "$MODEL" ]] && fail "Model name required."
                    ;;
                *) MODEL="qwen3.5:4b" ;;
            esac
            ;;

        2)
            # ── LM Studio ─────────────────────────────────────────────────
            API_BASE_URL="http://localhost:1234/v1"
            API_KEY="lm-studio"

            echo ""
            log_info "LM Studio serves whichever model you load in its GUI."
            read -rp "  Model name (as shown in LM Studio): " MODEL
            [[ -z "$MODEL" ]] && fail "Model name required. Load a model in LM Studio first."
            log_info "Make sure LM Studio is running with this model loaded."
            ;;

        3)
            # ── OpenRouter ────────────────────────────────────────────────
            API_BASE_URL="https://openrouter.ai/api/v1"

            echo ""
            log_info "Get your API key at: https://openrouter.ai/keys"
            read -rp "  API key: " API_KEY
            [[ -z "$API_KEY" ]] && fail "API key required for OpenRouter."

            echo ""
            echo -e "  ${BOLD}Choose a model${NC}"
            echo ""
            echo -e "  ${BOLD}1${NC}  google/gemini-2.0-flash-001          ${DIM}Fast, free tier${NC}"
            echo -e "  ${BOLD}2${NC}  meta-llama/llama-3.3-70b-instruct   ${DIM}Strong, tool calling${NC}"
            echo -e "  ${BOLD}3${NC}  anthropic/claude-sonnet-4            ${DIM}Premium quality${NC}"
            echo -e "  ${BOLD}4${NC}  Custom                               ${DIM}Enter model ID${NC}"
            echo ""
            read -rp "  Model [1-4]: " MODEL_CHOICE

            case "${MODEL_CHOICE:-1}" in
                1) MODEL="google/gemini-2.0-flash-001" ;;
                2) MODEL="meta-llama/llama-3.3-70b-instruct" ;;
                3) MODEL="anthropic/claude-sonnet-4" ;;
                4)
                    read -rp "  Model ID (from openrouter.ai/models): " MODEL
                    [[ -z "$MODEL" ]] && fail "Model ID required."
                    ;;
                *) MODEL="google/gemini-2.0-flash-001" ;;
            esac
            ;;

        4)
            # ── Groq ──────────────────────────────────────────────────────
            API_BASE_URL="https://api.groq.com/openai/v1"

            echo ""
            log_info "Get your API key at: https://console.groq.com/keys"
            read -rp "  API key: " API_KEY
            [[ -z "$API_KEY" ]] && fail "API key required for Groq."

            echo ""
            echo -e "  ${BOLD}Choose a model${NC}"
            echo ""
            echo -e "  ${BOLD}1${NC}  llama-3.3-70b-versatile    ${DIM}Balanced, tool calling${NC}"
            echo -e "  ${BOLD}2${NC}  llama-3.1-8b-instant       ${DIM}Fast, lightweight${NC}"
            echo -e "  ${BOLD}3${NC}  Custom                      ${DIM}Enter model ID${NC}"
            echo ""
            read -rp "  Model [1-3]: " MODEL_CHOICE

            case "${MODEL_CHOICE:-1}" in
                1) MODEL="llama-3.3-70b-versatile" ;;
                2) MODEL="llama-3.1-8b-instant" ;;
                3)
                    read -rp "  Model ID (from console.groq.com/docs/models): " MODEL
                    [[ -z "$MODEL" ]] && fail "Model ID required."
                    ;;
                *) MODEL="llama-3.3-70b-versatile" ;;
            esac
            ;;

        5)
            # ── OpenAI ────────────────────────────────────────────────────
            API_BASE_URL="https://api.openai.com/v1"

            echo ""
            log_info "Get your API key at: https://platform.openai.com/api-keys"
            read -rp "  API key: " API_KEY
            [[ -z "$API_KEY" ]] && fail "API key required for OpenAI."

            echo ""
            echo -e "  ${BOLD}Choose a model${NC}"
            echo ""
            echo -e "  ${BOLD}1${NC}  gpt-4o-mini      ${DIM}Fast, affordable${NC}"
            echo -e "  ${BOLD}2${NC}  gpt-4o           ${DIM}Strongest, vision + tools${NC}"
            echo -e "  ${BOLD}3${NC}  Custom            ${DIM}Enter model ID${NC}"
            echo ""
            read -rp "  Model [1-3]: " MODEL_CHOICE

            case "${MODEL_CHOICE:-1}" in
                1) MODEL="gpt-4o-mini" ;;
                2) MODEL="gpt-4o" ;;
                3)
                    read -rp "  Model ID: " MODEL
                    [[ -z "$MODEL" ]] && fail "Model ID required."
                    ;;
                *) MODEL="gpt-4o-mini" ;;
            esac
            ;;

        6)
            # ── Custom ────────────────────────────────────────────────────
            echo ""
            log_info "Enter your OpenAI-compatible endpoint details."
            echo ""
            read -rp "  API base URL (e.g. http://localhost:8000/v1): " API_BASE_URL
            [[ -z "$API_BASE_URL" ]] && fail "API base URL required."

            read -rp "  API key (leave empty if not required): " API_KEY
            API_KEY="${API_KEY:-none}"

            read -rp "  Model name: " MODEL
            [[ -z "$MODEL" ]] && fail "Model name required."
            ;;

        *)
            fail "Invalid choice. Run setup again."
            ;;
    esac
fi

# ── Write .env ────────────────────────────────────────────────────────────────

if [[ "$SKIP_PROVIDER_CONFIG" == "false" ]]; then
    cat > .env <<EOF
API_BASE_URL=${API_BASE_URL}
API_KEY=${API_KEY}

USE_MOCK_CLIENT=false

MODEL=${MODEL}

# Voice and speech
LIVE_LISTENING_ENABLED=true
LIVE_SPEECH_ENABLED=true
SPEECH_COOLDOWN_MS=3000
EOF

    log_ok "Configured: ${MODEL} via ${API_BASE_URL}"
fi

# ── Backend check ─────────────────────────────────────────────────────────────

API_BASE=$(grep '^API_BASE_URL=' .env | sed 's/^API_BASE_URL=//' | sed 's|/v1$||')

log_step "Checking backend..."

if curl -sf "${API_BASE}/v1/models" &>/dev/null; then
    log_ok "Backend reachable: ${API_BASE}"
elif curl -sf "${API_BASE}/api/tags" &>/dev/null; then
    IS_OLLAMA=true
    log_ok "Backend reachable: ${API_BASE} (Ollama)"
else
    log_warn "Backend not reachable: ${API_BASE}"
    log_info "Start your backend, then run: uv run python src/main.py"
    log_info "Skipping model checks."
fi

# ── Models ────────────────────────────────────────────────────────────────────

MODELS=$(grep -E '^(MODEL|[A-Z_]+_MODEL)=' .env | sed 's/.*=//' | sort -u)

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
            log_warn "Model not found: $model (check your provider)"
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

# Voice: check that local-whisper is available
if command -v wh &>/dev/null; then
    if wh status &>/dev/null; then
        log_ok "Local Whisper: running (voice + mic)"
    else
        log_warn "Local Whisper: not running (start with: wh start)"
    fi
else
    log_info "Voice input requires local-whisper (optional)"
    log_info "Install: https://github.com/gabrimatic/local-whisper"
fi

# ── Register command ──────────────────────────────────────────────────────────

EYRA_DIR="$(cd "$(dirname "$0")" && pwd)"

log_step "Registering eyra command..."

# Executable in ~/.local/bin (already on PATH for most setups)
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"

cat > "$BIN_DIR/eyra" <<LAUNCHER
#!/bin/bash
cd "$EYRA_DIR" && exec uv run python src/main.py "\$@"
LAUNCHER
chmod +x "$BIN_DIR/eyra"

# Alias in shell rc as fallback (matches local-whisper pattern)
EYRA_ALIAS="alias eyra='$BIN_DIR/eyra'"
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
    if [[ -f "$rc" ]]; then
        sed -i '' '/# eyra/d' "$rc"
        echo "$EYRA_ALIAS # eyra" >> "$rc"
    fi
done

log_ok "eyra command registered"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}  Setup complete!${NC}"
echo ""
echo -e "  ${BOLD}Run:${NC} eyra"
echo ""
