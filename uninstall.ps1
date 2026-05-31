param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "ReadAloudTTS"),
    [switch]$RemoveAppData,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*ReadAloudTTS.ahk*" } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

$startupDir = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDir "ReadAloudTTS.lnk"
Remove-Item -LiteralPath $shortcutPath -Force -ErrorAction SilentlyContinue

if ($RemoveAppData -and (Test-Path -LiteralPath $InstallDir)) {
    if (-not $Force) {
        Write-Host "This will remove the installed app folder:"
        Write-Host $InstallDir
        $answer = Read-Host "Type YES to remove it"
        if ($answer -ne "YES") {
            Write-Host "Kept installed app folder."
            Write-Host "ReadAloudTTS startup shortcut and running helper were removed."
            exit 0
        }
    }
    Remove-Item -LiteralPath $InstallDir -Recurse -Force
    Write-Host "Removed installed app folder: $InstallDir"
} else {
    Write-Host "Kept installed app folder: $InstallDir"
    Write-Host "Run with -RemoveAppData to remove app files, voices, logs, and local config."
}

Write-Host "ReadAloudTTS uninstalled."
