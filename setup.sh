#!/bin/bash
set -e

# ── Requirements ──────────────────────────────────────────────────────────────

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "macOS required." && exit 1
fi

if [[ "$(uname -m)" != "arm64" ]]; then
    echo "Apple Silicon required." && exit 1
fi

if ! python3 -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
    echo "Python 3.11 or higher is required."
    echo "Install it: brew install python@3.11"
    exit 1
fi

if ! command -v uv &>/dev/null; then
    echo "uv is required. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source "$HOME/.local/bin/env" 2>/dev/null || export PATH="$HOME/.local/bin:$PATH"
fi

# ── Dependencies ──────────────────────────────────────────────────────────────

echo "Installing dependencies..."
uv sync

# ── Models ────────────────────────────────────────────────────────────────────

echo "Downloading spaCy language model..."
uv run python -m spacy download en_core_web_sm

# ── Configuration ─────────────────────────────────────────────────────────────

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "Created .env from .env.example."
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "Setup complete."
echo ""
echo "Run: uv run python src/main.py"
