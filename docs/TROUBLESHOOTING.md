# Troubleshooting

## Ctrl + Right-click says no selected text found

The active app may not support normal copy, the selected area may not contain text, or the app may block clipboard access.

Try selecting text in a plain text editor first. If it works there, the issue is app-specific.

## Normal right-click behavior changed

Normal right-click should still work. The helper only binds `Ctrl + Right-click`. If an app already uses that gesture, choose a different selection method in that app or exit the helper from the tray.

## Piper failed

Open the logs from the tray menu. Common causes:

- The selected voice was not downloaded.
- Piper was not installed in the app virtual environment.
- The voice model file and voice config file do not match.
- Antivirus or endpoint policy blocked a local executable.

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\download_voices.ps1
```

from the repository folder, or run the installed copy from `%LOCALAPPDATA%\ReadAloudTTS`.

## AutoHotkey is missing

Install AutoHotkey v2, then rerun `install.ps1`. If `winget` is available, the installer can attempt to install AutoHotkey automatically.

## Python is missing

Install Python 3.12 or newer and rerun `install.ps1`.

## Clipboard content changed

The helper saves the previous clipboard, copies the selection, then restores the saved clipboard. Some clipboard managers may still record the temporary selection.

## Voice download fails

Check network access, proxy settings, and whether the selected model URL is reachable. The repository does not include fallback bundled voices.
