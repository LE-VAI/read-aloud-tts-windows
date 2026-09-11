@echo off
setlocal enabledelayedexpansion
set "REPO=%~dp0.."
"%USERPROFILE%\AppData\Local\Programs\AutoHotkey\v2\AutoHotkey64.exe" /ErrorStdOut /validate "%REPO%\src\ReadAloudTTS.ahk" 1> "%REPO%\tmp\validate_out.txt" 2> "%REPO%\tmp\validate_err.txt"
echo EXIT!ERRORLEVEL!
endlocal
