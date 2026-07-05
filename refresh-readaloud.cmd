@echo off
REM ReadAloudTTS — one-click refresh helper.
REM Double-click this file if Home stops playing speech.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0refresh-readaloud.ps1" %*
pause
