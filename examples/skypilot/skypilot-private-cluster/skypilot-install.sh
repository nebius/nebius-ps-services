#!/bin/bash
# skypilot-install.sh: Idempotent installer for SkyPilot with Nebius support
set -euo pipefail

VENV_DIR="$HOME/venvs/skypilot"
PYTHON_BIN="python3"

# Create venv directory if it doesn't exist
mkdir -p "$HOME/venvs"

# Create virtual environment if not present
if [ ! -d "$VENV_DIR" ]; then
  echo "Creating Python virtual environment at $VENV_DIR..."
  $PYTHON_BIN -m venv "$VENV_DIR"
else
  echo "Virtual environment already exists at $VENV_DIR."
fi

# Activate the virtual environment
source "$VENV_DIR/bin/activate"


# Upgrade pip first
pip install --upgrade pip

# Install/upgrade SkyPilot with Nebius support
pip install --upgrade "skypilot[nebius]"

echo "SkyPilot installation complete. To activate the environment in the future, run:"
echo "  source $VENV_DIR/bin/activate"
echo

echo "To verify your setup, run:"
echo "  sky check nebius"
