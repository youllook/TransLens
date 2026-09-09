@echo off
rem NOTE: keep rem lines ASCII-only and switch the console to UTF-8 first --
rem       cmd otherwise parses the UTF-8 bytes of Chinese text as commands.
chcp 65001 >nul
rem TransLens launcher: install missing packages, then start without a console window.
cd /d "%~dp0"
python -c "import winsdk, PIL, requests, pyaudiowpatch, onnxruntime, numpy, opencc" 2>nul
if errorlevel 1 (
    echo [TransLens] 安裝相依套件...
    python -m pip install -r requirements.txt
)
rem Silero VAD model (~2MB): fetch it up front so the first subtitle run does not wait.
rem If the download fails we say so; subtitles still work with the energy-based VAD.
if not exist "models\silero_vad.onnx" (
    echo [TransLens] 下載 Silero VAD 模型...
    python -c "from engines import vad; vad.ensure_model(progress=lambda d,t,s: None)" 2>nul
    if not exist "models\silero_vad.onnx" echo [TransLens] 警告：VAD 模型下載失敗，字幕模式將退回能量式 VAD。之後可執行 install_vad.bat 重試。
)
start "" pythonw translens.py %*
