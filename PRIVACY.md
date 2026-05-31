# Privacy

ReadAloudTTS is designed for local selected-text read aloud on Windows.

## Text handling

When you press `Ctrl + Right-click`, the helper sends a normal copy command, reads the temporary clipboard text, restores the previous clipboard contents, writes the selected text to a temporary local input file, and asks Piper to synthesize speech locally. The temporary input file is deleted after the Python helper reads it.

## Network use

The read-aloud flow does not send selected text to a cloud service.

Network access is used only when installing dependencies or downloading voice models. Voice download URLs are shown in `download_voices.ps1`.

## Local files

After installation, the app may create:

- `config.json` for local settings.
- `logs/` for local diagnostic logs.
- `tmp/` for temporary selected-text input files and generated WAV chunks.
- `voices/` for downloaded Piper voice files.
- `.venv/` for the local Python environment.

These files are local runtime data and are excluded from the repository.

## Clipboard note

Clipboard managers and security tools can observe clipboard changes. If that matters for your use case, do not use this tool with sensitive selected text.
