@echo off
rem TransLens 啟動器：缺套件就自動安裝，然後以無主控台方式啟動
cd /d "%~dp0"
python -c "import winsdk, PIL, requests, pyaudiowpatch, onnxruntime, numpy, opencc" 2>nul
if errorlevel 1 (
    echo [TransLens] 安裝相依套件...
    python -m pip install -r requirements.txt
)
rem Silero VAD 模型（約 2MB）：沒有就先抓，讓第一次開字幕不用等。
rem 抓不到會明講，字幕模式仍可用但會退回能量式 VAD（分不出音樂與人聲）。
if not exist "models\silero_vad.onnx" (
    echo [TransLens] 下載 Silero VAD 模型...
    python -c "from engines import vad; vad.ensure_model(progress=lambda d,t,s: None)" 2>nul
    if not exist "models\silero_vad.onnx" echo [TransLens] 警告：VAD 模型下載失敗，字幕模式將退回能量式 VAD。之後可執行 install_vad.bat 重試。
)
start "" pythonw translens.py %*
