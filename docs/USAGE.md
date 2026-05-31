# Usage

## Start the helper

The installer creates a startup shortcut and starts the helper. If you need to start it manually, run the installed `ReadAloudTTS.ahk` file with AutoHotkey v2 from `%LOCALAPPDATA%\ReadAloudTTS`.

## Read selected text

1. Select text in an app.
2. Press `Ctrl + Right-click`.
3. Wait for the tray notification.

The app must support normal copy. If copy is blocked, the helper cannot read the selection.

## Stop speech

Press `Ctrl + Alt + Space`.

You can also use the tray menu and choose `Stop`.

## Tray menu

The tray menu includes:

- `Read Selection`.
- `Stop`.
- `Voice`.
- `Open Config`.
- `Open Logs`.
- `Exit`.

## Change voices

Use the tray menu's `Voice` submenu. Voice changes update the installed `config.json`.

If a selected voice has not been downloaded yet, speech will fail with a clear message. Run `download_voices.ps1` and review the voice license notes before downloading.

## Config

The installer creates `config.json` from `config.example.json`.

Important settings:

- `current_voice`: selected voice id.
- `max_chars`: maximum selected text length to process.
- `chunk_chars`: approximate chunk size sent to Piper.
- `voices`: voice labels and local model paths.
