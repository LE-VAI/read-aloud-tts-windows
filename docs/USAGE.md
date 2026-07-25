# Usage

## Start the helper

The installer creates a startup shortcut and starts the helper. If you need to start it manually, run the installed `ReadAloudTTS.ahk` file with AutoHotkey v2 from `%LOCALAPPDATA%\ReadAloudTTS`.

## Read selected text

1. Select text in an app.
2. Press `Home` (or `Ctrl + Right-click` as an alternative).
3. Wait for the tray notification.

The app must support normal copy. If copy is blocked, the helper cannot read the selection.

## Stop speech

Press `F6`.

You can also use the tray menu and choose `Stop`.

## Tray menu

The tray menu includes:

- `Read Selection`.
- `Stop`.
- `Voice`.
- `Open Config`.
- `Open Logs`.
- `Show Transcript`.
- `Restart Daemon`.
- `Exit`.

## Change voices

Use the tray menu's `Voice` submenu. Voice changes update the installed `config.json`.

If a selected voice has not been downloaded yet, speech will fail with a clear message. Run `download_voices.ps1` and review the voice license notes before downloading.

## Config

The installer creates `config.json` from `config.example.json`.

Important settings:

- `current_voice`: selected voice id.
- `max_chars`: maximum selected text length to process.
- `chunk_chars`: approximate chunk size sent to Piper (default 600 — smaller chunks mean faster first audio).
- `sentence_silence`: seconds of silence after each sentence (default 0.5).
- `inter_chunk_pause`: seconds of silence between chunk playbacks (default 0.3).
- `length_scale`: overall speech speed (1.0 = natural, 0.8 = slower, 1.2 = faster).
- `noise_scale`: pitch/prosody variation (0.667 default).
- `noise_w`: phoneme duration jitter (0.8 default).
- `voices`: voice labels and local model paths.
