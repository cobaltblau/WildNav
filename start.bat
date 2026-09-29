@echo off
rem Start a local web server for WildNav and open it in the browser.
cd /d "%~dp0"
rem open the browser after 2 s, once the server below is running
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start "" http://localhost:8765/"
echo WildNav runs at http://localhost:8765/  -  close this window to stop the server.
python -m http.server 8765
