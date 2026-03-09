@echo off
chcp 65001 > nul
echo ================================
echo  Antigravity 學校環境自動安裝
echo ================================
echo.

set PROJECT=C:\皮克敏\antigravity_bridge

:: 確認資料夾存在
if not exist "%PROJECT%" (
    echo [錯誤] 找不到 %PROJECT%
    echo 請先 git clone 專案
    pause
    exit
)

cd /d %PROJECT%

:: 建立 venv
if not exist "venv" (
    echo [1/3] 建立虛擬環境...
    python -m venv venv
) else (
    echo [1/3] 虛擬環境已存在，跳過
)

:: 安裝套件
echo [2/3] 安裝所有套件（可能需要幾分鐘）...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt

:: 完成
echo.
echo [3/3] 安裝完成！
echo 現在請執行 start_school.bat 啟動程式
echo.
pause