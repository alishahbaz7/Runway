@echo off
REM Double-click this file (or run it) to start the ProInfo server.
REM It opens the dashboard + packet parser on http://127.0.0.1:8080
cd /d "%~dp0"
echo Starting ProInfo server...  (close this window or press Ctrl+C to stop)
echo Dashboard: http://127.0.0.1:8080
echo Parser:    http://127.0.0.1:8080/parser.html
python main.py
pause
