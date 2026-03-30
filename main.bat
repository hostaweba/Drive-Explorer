@echo off
REM Activate virtual environment
call "_WhichFile\scripts\activate"

REM Run the Python script
main.py

REM Friendly message pointing to the line above
echo.
echo.
echo     ------------------------------
echo     ^^^ Look, is everything OK?
echo     ------------------------------
pause >nul
cls

REM List Python files
echo.
echo ----------- 
dir /b *.py
echo ----------- 

REM List Batch files
echo.
echo ----------- 
dir /b *.bat
echo ----------- 
echo.


cmd