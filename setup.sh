# # #!/bin/bash

# # # Create a virtual environment
# # python3.11 -m venv .venv

# # # Activate the virtual environment
# # source .venv/bin/activate

# # # Upgrade pip
# # pip3 install --upgrade pip

# # # Install the required packages
# # pip3 install -r requirements.txt

# # # Upgrade the installed packages
# # pip3 install --upgrade -r requirements.txt

# # echo "Setup complete. Virtual environment created and packages installed."

# # python3 src/main.py


# uv init

# uv add --requirements requirements.txt       

# uv lock --upgrade && uv sync

# uv add pip

# uv run python -m spacy download en_core_web_sm

# wget -O src/modes/voice/models/tiny.en.pt https://openaipublic.azureedge.net/main/whisper/models/d3dd57d32accea0b295c96e26691aa14d8822fac7d9d27d5dc00b4ca2826dd03/tiny.en.pt