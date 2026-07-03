param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "ReadAloudTTS"),
    [switch]$SkipVoiceDownload,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"

function Get-PythonCommand {
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @($pyLauncher.Source, "-3.12")
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    throw "Python was not found. Install Python 3.12 or newer and rerun this script."
}

function Get-AutoHotkeyExe {
    $candidates = @(
        (Join-Path $env:ProgramFiles "AutoHotkey\v2\AutoHotkey64.exe"),
        (Join-Path $env:ProgramFiles "AutoHotkey\v2\AutoHotkey.exe"),
        (Join-Path $env:ProgramFiles "AutoHotkey\AutoHotkey64.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\AutoHotkey\v2\AutoHotkey64.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\AutoHotkey\v2\AutoHotkey.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\AutoHotkey\AutoHotkey64.exe")
    )

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    foreach ($name in @("AutoHotkey64.exe", "AutoHotkey.exe")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -and (Test-Path -LiteralPath $command.Source)) {
            return $command.Source
        }
    }

    return $null
}

function Stop-ExistingHelper {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*ReadAloudTTS.ahk*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Invoke-Python {
    param(
        [string[]]$PythonCommand,
        [string[]]$Arguments
    )

    $exe = $PythonCommand[0]
    $prefix = @()
    if ($PythonCommand.Count -gt 1) {
        $prefix = $PythonCommand[1..($PythonCommand.Count - 1)]
    }
    & $exe @prefix @Arguments
}

$sourceDir = Split-Path -Parent $PSCommandPath
$srcDir = Join-Path $sourceDir "src"

if (-not (Test-Path -LiteralPath (Join-Path $srcDir "ReadAloudTTS.ahk"))) {
    throw "Source file missing: src\ReadAloudTTS.ahk"
}
if (-not (Test-Path -LiteralPath (Join-Path $srcDir "speak.py"))) {
    throw "Source file missing: src\speak.py"
}
if (-not (Test-Path -LiteralPath (Join-Path $srcDir "speak_server.py"))) {
    throw "Source file missing: src\speak_server.py"
}

Write-Host "Installing ReadAloudTTS to $InstallDir"
Stop-ExistingHelper

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
foreach ($folder in @("logs", "tmp", "voices")) {
    New-Item -ItemType Directory -Path (Join-Path $InstallDir $folder) -Force | Out-Null
}

$copyMap = @(
    @{ Source = Join-Path $srcDir "ReadAloudTTS.ahk"; Target = Join-Path $InstallDir "ReadAloudTTS.ahk" },
    @{ Source = Join-Path $srcDir "speak.py"; Target = Join-Path $InstallDir "speak.py" },
    @{ Source = Join-Path $srcDir "speak_server.py"; Target = Join-Path $InstallDir "speak_server.py" },
    @{ Source = Join-Path $sourceDir "download_voices.ps1"; Target = Join-Path $InstallDir "download_voices.ps1" },
    @{ Source = Join-Path $sourceDir "uninstall.ps1"; Target = Join-Path $InstallDir "uninstall.ps1" },
    @{ Source = Join-Path $sourceDir "config.example.json"; Target = Join-Path $InstallDir "config.example.json" }
)

foreach ($item in $copyMap) {
    Copy-Item -LiteralPath $item.Source -Destination $item.Target -Force
}

$configTarget = Join-Path $InstallDir "config.json"
if (-not (Test-Path -LiteralPath $configTarget)) {
    Copy-Item -LiteralPath (Join-Path $sourceDir "config.example.json") -Destination $configTarget
    Write-Host "Created config: $configTarget"
} else {
    Write-Host "Keeping existing config: $configTarget"
}

$ahkExe = Get-AutoHotkeyExe
if (-not $ahkExe) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "AutoHotkey v2 not found. Installing AutoHotkey with winget..."
        & $winget.Source install --exact --id AutoHotkey.AutoHotkey --source winget --accept-source-agreements --accept-package-agreements --silent
        $ahkExe = Get-AutoHotkeyExe
    }
}
if (-not $ahkExe) {
    throw "AutoHotkey v2 was not found. Install AutoHotkey v2 and rerun this script."
}
Write-Host "Using AutoHotkey: $ahkExe"

$pythonCommand = Get-PythonCommand
$venvDir = Join-Path $InstallDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating Python virtual environment..."
    Invoke-Python -PythonCommand $pythonCommand -Arguments @("-m", "venv", $venvDir)
}

Write-Host "Installing Piper TTS into the local app environment..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install "piper-tts==1.4.2"

if (-not $SkipVoiceDownload) {
    Write-Host ""
    Write-Host "Voice models are downloaded separately and have their own licenses."
    Write-Host "Review docs\VOICE_LICENSING.md before commercial or public use."
    $answer = Read-Host "Download the default Lessac voice now? Type YES to continue"
    if ($answer -eq "YES") {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $InstallDir "download_voices.ps1") -InstallDir $InstallDir -Voice "en_US-lessac-medium"
    } else {
        Write-Host "Skipped voice download. Run download_voices.ps1 later when ready."
    }
}

$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "ReadAloudTTS.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $ahkExe
$shortcut.Arguments = "`"$(Join-Path $InstallDir "ReadAloudTTS.ahk")`""
$shortcut.WorkingDirectory = $InstallDir
$shortcut.Description = "Read selected text aloud with local Piper TTS"
$shortcut.Save()
Write-Host "Startup shortcut: $shortcutPath"

if (-not $NoStart) {
    Write-Host "Starting helper..."
    Start-Process -FilePath $ahkExe -ArgumentList "`"$(Join-Path $InstallDir "ReadAloudTTS.ahk")`"" -WorkingDirectory $InstallDir
}

Write-Host ""
Write-Host "ReadAloudTTS installed."
Write-Host "Use Ctrl+Right-click on selected text to read it aloud."
Write-Host "Use Ctrl+Alt+Space to stop speech."
