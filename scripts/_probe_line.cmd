@echo off
setlocal enabledelayedexpansion
set "REPO=%~dp0.."
set "AHSROOT=%LOCALAPPDATA%\ReadAloudTTS"
if exist "%REPO%\tmp\probe_line_result.txt" del "%REPO%\tmp\probe_line_result.txt"
"%AHSROOT%\.venv\Scripts\python.exe" --version >nul 2>&1
"%USERPROFILE%\AppData\Local\Programs\AutoHotkey\v2\AutoHotkey64.exe" /ErrorStdOut "%REPO%\scripts\probe_line_maturation.ahk" 2> "%REPO%\tmp\probe_line_err.txt"
echo EXIT!ERRORLEVEL!
if exist "%REPO%\tmp\probe_line_result.txt" type "%REPO%\tmp\probe_line_result.txt"
if exist "%REPO%\tmp\probe_line_err.txt" type "%REPO%\tmp\probe_line_err.txt"
endlocal
