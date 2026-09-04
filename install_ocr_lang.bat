@echo off
rem TransLens: install Japanese + English Windows OCR language packs (UAC prompt will appear).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_ocr_lang.ps1" %*
echo.
pause
