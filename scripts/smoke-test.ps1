$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = Split-Path -Parent $scriptDir
$repoGitPath = $repoRoot -replace "\\", "/"

function Invoke-Git {
    & git -c "safe.directory=$repoGitPath" @args
}

function Fail {
    param([string]$Message)
    Write-Host "Smoke test failed: $Message" -ForegroundColor Red
    exit 1
}

function Test-PowerShellSyntax {
    param([string]$Path)
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors -and $errors.Count -gt 0) {
        $messages = ($errors | ForEach-Object { $_.Message }) -join "; "
        Fail "PowerShell parse error in ${Path}: $messages"
    }
}

$requiredFiles = @(
    "README.md",
    "LICENSE",
    "NOTICE.md",
    "SECURITY.md",
    "PRIVACY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    ".gitignore",
    "config.example.json",
    "install.ps1",
    "uninstall.ps1",
    "download_voices.ps1",
    "src\ReadAloudTTS.ahk",
    "src\speak.py",
    "scripts\sanitize-check.ps1",
    "scripts\smoke-test.ps1",
    "docs\USAGE.md",
    "docs\TROUBLESHOOTING.md",
    "docs\VOICE_LICENSING.md"
)

foreach ($relative in $requiredFiles) {
    $path = Join-Path $repoRoot $relative
    if (-not (Test-Path -LiteralPath $path)) {
        Fail "Missing required file: $relative"
    }
}

foreach ($relative in @("install.ps1", "uninstall.ps1", "download_voices.ps1", "scripts\sanitize-check.ps1", "scripts\smoke-test.ps1")) {
    Test-PowerShellSyntax -Path (Join-Path $repoRoot $relative)
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) {
    $python = Get-Command python -ErrorAction SilentlyContinue
}
if (-not $python) {
    Fail "Python was not found for syntax validation."
}
& $python.Source -m py_compile (Join-Path $repoRoot "src\speak.py")
if ($LASTEXITCODE -ne 0) {
    Fail "Python syntax validation failed."
}
$pythonCache = Join-Path $repoRoot "src\__pycache__"
if (Test-Path -LiteralPath $pythonCache) {
    Remove-Item -LiteralPath $pythonCache -Recurse -Force
}

$requiredIgnores = @(
    "voices/",
    "*.onnx",
    "*.onnx.json",
    "*.wav",
    "*.log",
    "logs/",
    "temp/",
    "tmp/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".DS_Store",
    "Thumbs.db",
    "config.json",
    "local-config.json",
    "*.bak",
    "*.tmp",
    "dist/",
    "build/"
)

$gitignore = Get-Content -LiteralPath (Join-Path $repoRoot ".gitignore")
foreach ($pattern in $requiredIgnores) {
    if ($gitignore -notcontains $pattern) {
        Fail ".gitignore missing required pattern: $pattern"
    }
}

$forbiddenFiles = Get-ChildItem -LiteralPath $repoRoot -Recurse -File -Force |
    Where-Object {
        $_.FullName -notmatch "\\.git\\" -and
        (
            $_.Extension.ToLowerInvariant() -in @(".onnx", ".wav", ".log", ".pyc", ".pyo") -or
            $_.Name.ToLowerInvariant().EndsWith(".onnx.json")
        )
    }
if ($forbiddenFiles.Count -gt 0) {
    $list = ($forbiddenFiles | ForEach-Object { $_.FullName }) -join "; "
    Fail "Forbidden artifact files are present: $list"
}

Push-Location $repoRoot
try {
    $isGitRepo = Test-Path -LiteralPath (Join-Path $repoRoot ".git")
    if ($isGitRepo) {
        $trackedForbidden = Invoke-Git ls-files | Where-Object {
            $_ -match '(^|/)(voices|logs|temp|tmp|\.venv|venv|__pycache__|dist|build)/' -or
            $_ -match '\.(onnx|wav|log|pyc|pyo)$' -or
            $_ -match '\.onnx\.json$' -or
            $_ -in @("config.json", "local-config.json")
        }
        if ($trackedForbidden) {
            Fail "Forbidden tracked files: $($trackedForbidden -join ', ')"
        }

        $oldPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        Invoke-Git rev-parse --verify HEAD *> $null
        $hasCommit = ($LASTEXITCODE -eq 0)
        $ErrorActionPreference = $oldPreference
        if ($hasCommit) {
            $status = Invoke-Git status --short
            if ($status) {
                Fail "Git status is not clean: $($status -join '; ')"
            }
        }
    }
} finally {
    Pop-Location
}

Write-Host "Smoke test passed."
Write-Host "Validated $($requiredFiles.Count) required files."
