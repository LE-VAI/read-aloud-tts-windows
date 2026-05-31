param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "ReadAloudTTS"),
    [ValidateSet("en_US-lessac-medium", "en_US-amy-medium", "en_US-hfc_female-medium", "all")]
    [string[]]$Voice = @("en_US-lessac-medium"),
    [switch]$AcceptVoiceLicense
)

$ErrorActionPreference = "Stop"

$voiceCatalog = @{
    "en_US-lessac-medium" = @{
        Label = "Lessac - warm"
        ModelUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx"
        ConfigUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"
        ModelName = "en_US-lessac-medium.onnx"
        ConfigName = "en_US-lessac-medium.onnx.json"
        ModelCard = "https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/lessac/medium/MODEL_CARD"
        LicenseNote = "Review the Lessac model card and linked dataset license before use."
    }
    "en_US-amy-medium" = @{
        Label = "Amy - clear"
        ModelUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx"
        ConfigUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/en_US-amy-medium.onnx.json"
        ModelName = "en_US-amy-medium.onnx"
        ConfigName = "en_US-amy-medium.onnx.json"
        ModelCard = "https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/amy/medium/MODEL_CARD"
        LicenseNote = "Review the Amy model card and linked voice source before use."
    }
    "en_US-hfc_female-medium" = @{
        Label = "HFC Female - soft"
        ModelUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/hfc_female/medium/en_US-hfc_female-medium.onnx"
        ConfigUrl = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/hfc_female/medium/en_US-hfc_female-medium.onnx.json"
        ModelName = "en_US-hfc_female-medium.onnx"
        ConfigName = "en_US-hfc_female-medium.onnx.json"
        ModelCard = "https://huggingface.co/rhasspy/piper-voices/blob/main/en/en_US/hfc_female/medium/MODEL_CARD"
        LicenseNote = "Sensitive: the hfc_female model card references CC BY-NC-SA 4.0 dataset licensing."
    }
}

if ($Voice -contains "all") {
    $selectedVoices = @("en_US-lessac-medium", "en_US-amy-medium", "en_US-hfc_female-medium")
} else {
    $selectedVoices = $Voice
}

Write-Host "Voice models are third-party files with separate model cards and licenses."
Write-Host "This script downloads selected voices to the local installed app folder only."
Write-Host "No voice files should be committed to this repository."
Write-Host ""
foreach ($voiceId in $selectedVoices) {
    $voiceInfo = $voiceCatalog[$voiceId]
    Write-Host "$voiceId - $($voiceInfo.Label)"
    Write-Host "  Model card: $($voiceInfo.ModelCard)"
    Write-Host "  License note: $($voiceInfo.LicenseNote)"
}
Write-Host ""

if (-not $AcceptVoiceLicense) {
    $answer = Read-Host "Type YES after reviewing the voice licensing notes"
    if ($answer -ne "YES") {
        Write-Host "Voice download cancelled."
        exit 1
    }
}

$voicesDir = Join-Path $InstallDir "voices"
New-Item -ItemType Directory -Path $voicesDir -Force | Out-Null

foreach ($voiceId in $selectedVoices) {
    $voiceInfo = $voiceCatalog[$voiceId]
    $downloads = @(
        @{ Url = $voiceInfo.ModelUrl; Name = $voiceInfo.ModelName },
        @{ Url = $voiceInfo.ConfigUrl; Name = $voiceInfo.ConfigName }
    )

    foreach ($file in $downloads) {
        $target = Join-Path $voicesDir $file.Name
        if ((Test-Path -LiteralPath $target) -and ((Get-Item -LiteralPath $target).Length -gt 1000)) {
            Write-Host "Already present: $($file.Name)"
            continue
        }

        Write-Host "Downloading $($file.Name)"
        $partial = "$target.partial"
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        Invoke-WebRequest -Uri $file.Url -OutFile $partial -Headers @{ "User-Agent" = "ReadAloudTTS/0.1" }
        Move-Item -LiteralPath $partial -Destination $target -Force
    }
}

Write-Host "Voice download complete: $voicesDir"
