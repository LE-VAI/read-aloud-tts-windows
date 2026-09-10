@echo off
setlocal enabledelayedexpansion
cd /d "%LOCALAPPDATA%\ReadAloudTTS"
if exist "<repo>\tmp\probe_line_result.txt" del "<repo>\tmp\probe_line_result.txt"
"%USERPROFILE%\AppData\Local\Programs\AutoHotkey\v2\AutoHotkey64.exe" /ErrorStdOut "<repo>\scripts\probe_line_maturation.ahk" 2> "<repo>\tmp\probe_line_err.txt"
echo EXIT!ERRORLEVEL!
if exist "<repo>\tmp\probe_line_result.txt" type "<repo>\tmp\probe_line_result.txt"
if exist "<repo>\tmp\probe_line_err.txt" type "<repo>\tmp\probe_line_err.txt"
endlocal