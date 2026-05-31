$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = Split-Path -Parent $scriptDir

function Get-RelativePath {
    param([string]$Path)
    $rootFull = (Resolve-Path -LiteralPath $repoRoot).Path.TrimEnd("\") + "\"
    if (Test-Path -LiteralPath $Path) {
        $pathFull = (Resolve-Path -LiteralPath $Path).Path
    } else {
        $pathFull = [System.IO.Path]::GetFullPath($Path)
    }
    if ($pathFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $pathFull.Substring($rootFull.Length)
    }
    return $pathFull
}

$textExtensions = @(
    ".ahk", ".json", ".md", ".ps1", ".py", ".txt", ".yml", ".yaml", ".gitignore"
)

$textFiles = Get-ChildItem -LiteralPath $repoRoot -Recurse -File -Force |
    Where-Object {
        $_.FullName -notmatch "\\.git\\" -and
        (
            $textExtensions -contains $_.Extension.ToLowerInvariant() -or
            $_.Name -in @("LICENSE", "NOTICE", "README", ".gitignore")
        )
    }

$issues = New-Object System.Collections.Generic.List[object]

$artifactExtensions = @(".onnx", ".wav", ".log", ".pyc", ".pyo")
$artifactPathParts = @(
    "\\voices\\",
    "\\logs\\",
    "\\temp\\",
    "\\tmp\\",
    "\\.venv\\",
    "\\venv\\",
    "\\__pycache__\\",
    "\\.pytest_cache\\",
    "\\.mypy_cache\\",
    "\\dist\\",
    "\\build\\"
)

$repoFiles = Get-ChildItem -LiteralPath $repoRoot -Recurse -File -Force |
    Where-Object { $_.FullName -notmatch "\\.git\\" }

foreach ($file in $repoFiles) {
    $relative = Get-RelativePath $file.FullName
    $lowerFullName = $file.FullName.ToLowerInvariant()
    $extension = $file.Extension.ToLowerInvariant()

    if ($artifactExtensions -contains $extension -or $file.Name.ToLowerInvariant().EndsWith(".onnx.json")) {
        $issues.Add([pscustomobject]@{
            File = $relative
            Line = 0
            Kind = "Forbidden generated or downloaded artifact"
            Text = $file.Name
        })
    }

    foreach ($part in $artifactPathParts) {
        if ($lowerFullName.Contains($part)) {
            $issues.Add([pscustomobject]@{
                File = $relative
                Line = 0
                Kind = "Forbidden runtime/cache directory"
                Text = $part
            })
        }
    }
}

$dynamicTerms = @()
if ($env:USERNAME) {
    $dynamicTerms += [regex]::Escape($env:USERNAME)
}
if ($env:COMPUTERNAME) {
    $dynamicTerms += [regex]::Escape($env:COMPUTERNAME)
}

$privateTerms = @(
    ("AT" + "LAS"),
    ("VAI" + "-WORLD"),
    ("Power" + "Shell history"),
    ("shell command" + " history"),
    ("chat trans" + "cript")
)

$patterns = New-Object System.Collections.Generic.List[object]
$patterns.Add([pscustomobject]@{ Kind = "Absolute Windows path"; Regex = "\b[A-Za-z]:\\" })
$patterns.Add([pscustomobject]@{ Kind = "User profile path"; Regex = "\\Users\\[A-Za-z0-9._-]+\\" })
$patterns.Add([pscustomobject]@{ Kind = "Email address"; Regex = "\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b" })
$patterns.Add([pscustomobject]@{ Kind = "GitHub token pattern"; Regex = "gh[pousr]_[A-Za-z0-9_]{20,}" })
$patterns.Add([pscustomobject]@{ Kind = "GitHub fine-grained token pattern"; Regex = "github_pat_[A-Za-z0-9_]{20,}" })
$patterns.Add([pscustomobject]@{ Kind = "OpenAI key pattern"; Regex = "sk-[A-Za-z0-9]{20,}" })
$patterns.Add([pscustomobject]@{ Kind = "AWS access key pattern"; Regex = "AKIA[0-9A-Z]{16}" })
$patterns.Add([pscustomobject]@{ Kind = "Generic secret assignment"; Regex = "(?i)(api[_-]?key|access[_-]?token|secret|password|recovery[_-]?code)\s*[:=]\s*['""][^'""]+['""]" })
$financialTerms = @(
    ("seed" + " phrase"),
    ("private" + " key"),
    ("wallet" + " recovery"),
    ("mnemo" + "nic"),
    ("bank" + " account"),
    ("routing" + " number")
)
$financialRegex = "(?i)\b(" + (($financialTerms | ForEach-Object { [regex]::Escape($_) }) -join "|") + ")\b"
$patterns.Add([pscustomobject]@{ Kind = "Wallet or financial credential term"; Regex = $financialRegex })

foreach ($term in $dynamicTerms) {
    if ($term.Length -ge 3) {
        $patterns.Add([pscustomobject]@{ Kind = "Machine-specific identifier"; Regex = "(?i)\b$term\b" })
    }
}

foreach ($term in $privateTerms) {
    $patterns.Add([pscustomobject]@{ Kind = "Private/internal term"; Regex = [regex]::Escape($term) })
}

foreach ($file in $textFiles) {
    $relative = Get-RelativePath $file.FullName
    $lines = Get-Content -LiteralPath $file.FullName -ErrorAction Stop
    for ($index = 0; $index -lt $lines.Count; $index++) {
        $line = $lines[$index]
        foreach ($pattern in $patterns) {
            if ($line -match $pattern.Regex) {
                $issues.Add([pscustomobject]@{
                    File = $relative
                    Line = $index + 1
                    Kind = $pattern.Kind
                    Text = $line.Trim()
                })
            }
        }
    }
}

if ($issues.Count -gt 0) {
    Write-Host "Sanitization check failed:" -ForegroundColor Red
    foreach ($issue in $issues) {
        if ($issue.Line -gt 0) {
            Write-Host "$($issue.File):$($issue.Line) [$($issue.Kind)] $($issue.Text)"
        } else {
            Write-Host "$($issue.File) [$($issue.Kind)] $($issue.Text)"
        }
    }
    exit 1
}

Write-Host "Sanitization check passed."
Write-Host "Scanned $($textFiles.Count) text files and $($repoFiles.Count) repository files."
