#!/bin/bash

# Activate virtual environment
# Note: Linux venvs use 'bin' instead of 'scripts' and forward slashes
source "_WhichFile/bin/activate"

# Run the Python script
python3 main.py

# Friendly message pointing to the line above
echo ""
echo ""
echo "     ------------------------------"
echo "     ^^^ Look, is everything OK?"
echo "     ------------------------------"

# Pause until the user presses any key silently
read -n 1 -s -r -p ""

# Clear the screen
clear

# List Python files (-1 formats them in a single column)
# 2>/dev/null hides error messages if no files are found
echo ""
echo "----------- "
ls -1 *.py 2>/dev/null
echo "----------- "

# List Shell files (swapped .bat for .sh as the Linux equivalent)
echo ""
echo "----------- "
ls -1 *.sh 2>/dev/null
echo "----------- "
echo ""

# Keep the terminal session open
exec bash