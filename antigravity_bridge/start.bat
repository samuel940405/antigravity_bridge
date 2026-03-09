@echo off
chcp 65001 > nul
echo Antigravity Bridge Starting...
echo.

echo [1/3] Starting tunneld...
powershell -Command "Start-Process powershell -ArgumentList '-NoExit', '-Command', 'cd C:\皮克敏\antigravity_bridge; venv\Scripts\activate; py -m pymobiledevice3 remote tunneld' -Verb RunAs"

timeout /t 3 /nobreak > nul

echo [2/3] Starting bridge_server...
start "BridgeServer" cmd /k "cd /d C:\皮克敏\antigravity_bridge && venv\Scripts\activate.bat && py bridge_server.py"

timeout /t 3 /nobreak > nul

echo [3/3] Starting Streamlit...
start "Streamlit" cmd /k "cd /d C:\皮克敏\antigravity_bridge && venv\Scripts\activate.bat && python -m streamlit run app.py"

echo.
echo Done! Open http://localhost:8501
pause