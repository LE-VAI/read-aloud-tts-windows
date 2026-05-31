# Changelog

## 0.1.2 - Interactive capture and Unicode hardening

- Swallowed Ctrl+Right-click down/up in the AutoHotkey helper to avoid browser context menu leakage.
- Changed selected-text handoff to unique UTF-8-RAW temp input files per request.
- Added Python text sanitation for BOMs, control/format characters, and lone Unicode surrogates before Piper input.
- Normalized smart punctuation, skipped symbol-class characters, and added a Piper chunk timeout.

## 0.1.1 - Temp audio cleanup patch

- Added startup cleanup for stale `tmp/readaloud-*` folders and loose temp WAV files.
- Kept playback limited to WAV files generated during the current speech request.
- Added explicit current-run temp audio cleanup after playback and failure.

## 0.1.0 - Public source candidate

- Prepared source-only Windows ReadAloudTTS public candidate.
- Added AutoHotkey v2 tray and hotkey helper.
- Added Python Piper TTS runner with text chunking and temporary WAV cleanup.
- Added installer, uninstaller, voice downloader, documentation, and sanitization checks.
- Excluded voice models, logs, temp files, virtual environments, and local config from source control.
