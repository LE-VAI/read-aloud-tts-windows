# Contributing

Thanks for considering a contribution.

## Before opening a pull request

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\sanitize-check.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\smoke-test.ps1
python -m py_compile .\src\speak.py
git diff --check
```

Do not include:

- Downloaded voices.
- Generated WAV files.
- Logs.
- Local configs.
- Virtual environments.
- Local absolute paths.
- Credentials or tokens.
- Private selected text.

## Development notes

- Keep the repository source-only.
- Keep AutoHotkey v2 as the tray and hotkey layer.
- Keep Piper as the local TTS path.
- Preserve clipboard restoration behavior.
- Do not add a cloud TTS fallback.
