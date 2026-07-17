@echo off
REM Double-click this file (or run it) to start the ProInfo server.
REM It opens the dashboard + packet parser on http://127.0.0.1:8080
REM
REM The server always runs from the repo-local .venv (created on first run), never from
REM whatever "python" happens to be first on PATH — this machine has several Pythons and
REM a wrong one means missing dependencies at the worst moment (e.g. the XLS QC report).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating the virtual environment ^(first run only^)...
    python -m venv .venv
    if errorlevel 1 goto :err
)

REM Quick dependency probe; (re)install only when something is missing.
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, openpyxl, yaml, multipart, httpx" 2>nul
if errorlevel 1 (
    echo Installing dependencies into .venv ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto :err
)

echo Starting ProInfo server...  (close this window or press Ctrl+C to stop)
echo Dashboard: http://127.0.0.1:8080
echo Parser:    http://127.0.0.1:8080/parser.html
".venv\Scripts\python.exe" main.py
pause
exit /b 0

:err
echo.
echo Setup failed — check that Python 3.10+ is installed and on PATH.
pause
exit /b 1
