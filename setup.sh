#!/bin/bash
set -e

echo "Setting up Eyra..."

if ! command -v python3.11 &>/dev/null && ! python3 -c "import sys; assert sys.version_info >= (3,11)" 2>/dev/null; then
    echo "Python 3.11 or higher is required."
    exit 1
fi

if ! command -v uv &>/dev/null; then
    echo "uv is required. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

uv sync
uv run python -m spacy download en_core_web_sm

echo ""
echo "Setup complete."
echo "Copy .env.example to .env and fill in your values."
echo "Run: uv run python src/main.py"
