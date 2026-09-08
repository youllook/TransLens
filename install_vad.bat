@echo off
rem TransLens：安裝語音偵測（VAD）相依套件並預先下載 Silero 模型。
rem 平常不必手動跑這個 —— 第一次勾「🎧字幕」時程式會自己下載。
rem 想先把模型抓下來（例如要離線使用）才需要執行。
cd /d "%~dp0"
echo [TransLens] 安裝 onnxruntime 與 numpy...
python -m pip install onnxruntime numpy
if errorlevel 1 (
    echo [TransLens] 套件安裝失敗。沒有 onnxruntime 也能用，
    echo             但字幕模式會退回能量式 VAD（分不出音樂與人聲）。
    pause
    exit /b 1
)
echo [TransLens] 下載 Silero VAD 模型...
python -c "from engines import vad; print('OK:', vad.ensure_model(progress=lambda d,t,s: None))"
if errorlevel 1 (
    echo [TransLens] 模型下載失敗（可能是沒有網路或 GitHub 連不上）。
    echo             字幕模式仍可使用，會退回能量式 VAD。
    pause
    exit /b 1
)
echo [TransLens] 完成。
pause
