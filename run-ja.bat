@echo off
rem TransLens: start with audio subtitles ON, language locked to ja, VAD sensitivity = sensitive.
rem Same install/model-download steps as run.bat (this just calls it with extra arguments).
call "%~dp0run.bat" --subtitle --audio-lang ja --vad-sensitivity sensitive %*
