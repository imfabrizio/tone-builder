#!/bin/bash
# Avvio su macOS: usa il venv in ~/.venvs/tone-builder (fuori da OneDrive) o, se c'e', .venv nel progetto
cd "$(dirname "$0")"
if [ -f "$HOME/.venvs/tone-builder/bin/activate" ]; then source "$HOME/.venvs/tone-builder/bin/activate"
elif [ -f .venv/bin/activate ]; then source .venv/bin/activate
fi
python -m tonebuilder
