@echo off
rem TransLens 啟動器：缺套件就自動安裝，然後以無主控台方式啟動
cd /d "%~dp0"
python -c "import winsdk, PIL, requests" 2>nul
if errorlevel 1 (
    echo [TransLens] 安裝相依套件...
    python -m pip install -r requirements.txt
)
rem Silero VAD 模型（約 2MB）：有 onnxruntime 就順手抓下來，讓第一次開字幕不用等。
rem 抓不到也沒關係，字幕模式會退回能量式 VAD。
if not exist "models\silero_vad.onnx" (
    python -c "import onnxruntime" 2>nul && (
        echo [TransLens] 下載 Silero VAD 模型...
        python -c "from engines import vad; vad.ensure_model(progress=lambda d,t,s: None)" 2>nul
    )
)
start "" pythonw translens.py
