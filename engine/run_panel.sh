#!/bin/bash
# Starts the control panel. Run:  bash ~/projects/translation_workbench/engine/run_panel.sh
# It uses the normal Python if PyQt6 is installed there, and otherwise the Word Atlas
# virtual environment (Word Atlas also needs PyQt6, so it is installed there).
cd ~/projects/translation_workbench/engine
if python3 -c "import PyQt6" 2>/dev/null; then
    python3 control_panel.py "$@"
elif [ -x ~/projects/word_atlas/venv/bin/python ]; then
    ~/projects/word_atlas/venv/bin/python control_panel.py "$@"
else
    echo "PyQt6 was not found. Tell Claude and it will give you the install command."
fi
