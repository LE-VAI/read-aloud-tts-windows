<#
.SYNOPSIS
    One-click recovery for ReadAloudTTS.

.DESCRIPTION
    If Home stops playing speech (e.g. after sleep, reboot, or AHK reload
    desyncs from the TTS daemon), this script gets you back to normal in
    a few seconds:

    1. Sends a graceful quit to any live daemon (works regardless of
       process elevation — unlike taskkill).
    2. Waits for the daemon to exit and release its single-instance mutex.
    3. Launches a fresh daemon.
    4. Verifies the daemon is ready (Piper model loaded).

    Safe to run at any time, even if the daemon is already healthy.

.PARAMETER InstallDir
    ReadAloudTTS install folder. Defaults to %LOCALAPPDATA%\ReadAloudTTS.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\refresh-readaloud.ps1
#>

param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "ReadAloudTTS")
)

$ErrorActionPreference = "Stop"

$tmpDir      = Join-Path $InstallDir "tmp"
$reqPath     = Join-Path $tmpDir "request.json"
$respPath    = Join-Path $tmpDir "response.json"
$markerPath  = Join-Path $tmpDir "daemon_ready"
$pyExe       = Join-Path $InstallDir ".venv\Scripts\python.exe"
$logPath     = Join-Path $InstallDir "logs\readaloud.log"
$utf8NoBom   = [System.Text.UTF8Encoding]::new($false)

if (-not (Test-Path -LiteralPath $pyExe)) {
    Write-Host "Python environment not found at $pyExe" -ForegroundColor Red
    Write-Host "Run install.ps1 to set up ReadAloudTTS first." -ForegroundColor Yellow
    exit 1
}

New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

Write-Host ""
Write-Host "Refreshing ReadAloudTTS..." -ForegroundColor Cyan
Write-Host ""

# --- Step 1: Send graceful quit to any live daemon ---------------------------
Write-Host "  Sending quit to any running daemon..." -NoNewline
if (Test-Path -LiteralPath $respPath) { Remove-Item -LiteralPath $respPath -Force }
if (Test-Path -LiteralPath $reqPath)  { Remove-Item -LiteralPath $reqPath -Force }
[System.IO.File]::WriteAllText($reqPath, '{"action":"quit"}', $utf8NoBom)

$quitOk = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 100
    if (Test-Path -LiteralPath $respPath) { $quitOk = $true; break }
}
if ($quitOk) {
    Write-Host " acknowledged." -ForegroundColor Green
} else {
    Write-Host " no response (may already be stopped)." -ForegroundColor DarkGray
}

# --- Step 2: Wait for the mutex to release after exit ------------------------
Start-Sleep -Milliseconds 800

# --- Step 3: Clean up stale IPC files ---------------------------------------
foreach ($f in @($reqPath, $respPath, $markerPath)) {
    if (Test-Path -LiteralPath $f) { Remove-Item -LiteralPath $f -Force }
}

# --- Step 4: Launch a fresh daemon ------------------------------------------
Write-Host "  Starting fresh daemon..." -NoNewline
$proc = Start-Process -FilePath $pyExe -ArgumentList "speak.py", "--serve" `
    -WorkingDirectory $InstallDir -WindowStyle Hidden -PassThru

# --- Step 5: Wait for the readiness marker (Piper model load) ----------------
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path -LiteralPath $markerPath) { $ready = $true; break }
    if ($proc.HasExited) {
        Write-Host ""
        Write-Host "  Daemon process exited early (code $($proc.ExitCode))." -ForegroundColor Red
        if (Test-Path -LiteralPath $logPath) {
            Write-Host "  Last log line: $((Get-Content -LiteralPath $logPath -Tail 1).Trim())" -ForegroundColor DarkYellow
        }
        Write-Host ""
        Write-Host "  Check the log: $logPath" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ""
if ($ready) {
    $elapsed = $i + 1
    Write-Host ""
    Write-Host "  ReadAloudTTS is ready! (loaded in ${elapsed}s)" -ForegroundColor Green
    Write-Host "    Home  = read selected text" -ForegroundColor White
    Write-Host "    F6    = stop speech" -ForegroundColor White
    Write-Host ""
    Write-Host "    Daemon PID: $($proc.Id)" -ForegroundColor DarkGray
    if (Test-Path -LiteralPath $logPath) {
        Write-Host "    Last log:   $((Get-Content -LiteralPath $logPath -Tail 1).Trim())" -ForegroundColor DarkGray
    }
    Write-Host ""
    exit 0
} else {
    Write-Host ""
    Write-Host "  Daemon did not become ready in 30s." -ForegroundColor Red
    Write-Host "  Check the log: $logPath" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
