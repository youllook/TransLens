@echo off
rem TransLens 啟動器：缺套件就自動安裝，然後以無主控台方式啟動
cd /d "%~dp0"
python -c "import winsdk, PIL, requests" 2>nul
if errorlevel 1 (
    echo [TransLens] 安裝相依套件...
    python -m pip install -r requirements.txt
)
start "" pythonw translens.py
