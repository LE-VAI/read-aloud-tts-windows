# Troubleshooting

## Home does nothing / speech stops working

**Symptom:** You press `Home` on selected text and nothing plays. This typically happens after sleep, hibernate, reboot, or an AutoHotkey reload — the AHK overlay loses track of the TTS daemon, even though the daemon process may still be alive in the background.

**What's happening:** The daemon writes a `tmp\daemon_ready` marker file when it's warmed up. AHK checks this marker before speaking. If the marker gets deleted (e.g. AHK restarts and prunes stale state) while the daemon is still running, AHK spawns a new daemon — but the new process exits immediately because the old one holds a single-instance mutex. Home then silently does nothing.

**Quick fix — one-click refresh:**

Double-click `refresh-readaloud.cmd` in the install folder (`%LOCALAPPDATA%\ReadAloudTTS`), or run from PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\ReadAloudTTS\refresh-readaloud.ps1"
```

This sends a graceful quit to any stuck daemon, waits for it to exit, launches a fresh one, and verifies it's ready. It works regardless of process elevation and is safe to run at any time, even if the daemon is already healthy.

**Alternative fix — tray menu:**

Right-click the ReadAloudTTS tray icon and choose **Restart Daemon**. This does the same quit-and-restart cycle from within AHK.

**Why this should be rare now:** The daemon self-heals its readiness marker every 20ms (if anything deletes it, the daemon restores it immediately), and AHK pings the daemon before pruning the marker on startup. These two changes prevent the desync in the first place. The refresh helper is the safety net for edge cases.

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
