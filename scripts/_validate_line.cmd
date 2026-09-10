@echo off
setlocal enabledelayedexpansion
"%USERPROFILE%\AppData\Local\Programs\AutoHotkey\v2\AutoHotkey64.exe" /ErrorStdOut /validate "<repo>\src\ReadAloudTTS.ahk" 1> "<repo>\tmp\validate_out.txt" 2> "<repo>\tmp\validate_err.txt"
echo EXIT!ERRORLEVEL!
endlocal