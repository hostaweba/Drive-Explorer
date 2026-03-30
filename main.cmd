# 2>nul & goto :windows
#!/bin/bash

# ==========================================
#        MAC & LINUX (BASH) SECTION
# ==========================================
source "_WhichFile/bin/activate"
python3 main.py

echo ""
echo ""
echo "     ------------------------------"
echo "     ^^^ Look, is everything OK?"
echo "     ------------------------------"
read -n 1 -s -r -p ""
clear

echo ""
echo "----------- "
ls -1 *.py 2>/dev/null
echo "----------- "

echo ""
echo "----------- "
ls -1 *.sh *.bat *.cmd 2>/dev/null
echo "----------- "
echo ""

exec bash
exit 0

:windows
:: ==========================================
::           WINDOWS (CMD) SECTION
:: ==========================================
@echo off
call "_WhichFile\scripts\activate"

main.py

echo.
echo.
echo      ------------------------------
echo      ^^^ Look, is everything OK?
echo      ------------------------------
pause >nul
cls

echo.
echo ----------- 
dir /b *.py
echo ----------- 

echo.
echo ----------- 
dir /b *.bat *.sh *.cmd
echo ----------- 
echo.

cmd