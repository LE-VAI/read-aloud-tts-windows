# Changelog

## 0.2.0 - Performance and cadence improvements

### Performance
- Pipelined synthesis and playback: chunk N+1 is synthesized in a background thread while chunk N plays, hiding all but the first Piper cold start behind audio time.
- Merged short paragraphs into chunks up to the character budget instead of emitting one chunk per paragraph, cutting chunk count 3-5x for typical multi-paragraph selections.
- Raised default `chunk_chars` from 900 to 2000, reducing the number of Piper process launches per request.
- Switched Piper invocation to `sys.executable -m piper` to pin to the venv interpreter, eliminating a wrong-interpreter race from the standalone `piper.exe` zip-app launcher.

### Cadence and naturalness
- Added configurable `--sentence-silence` (default 0.5s) so Piper inserts a clean pause after every sentence instead of running sentences together.
- Added an inter-chunk pause (default 0.3s) between WAV playbacks so chunk boundaries do not sound like an abrupt mid-sentence merger.
- Preserved em-dashes as a spaced ` — ` so Piper reads them as a pause instead of a compound word.
- Verbalized common symbols (`$` to "dollars", `%` to "percent", `&` to "and", `+` to "plus", `=` to "equals", `@` to "at", `#` to "hashtag") instead of silently deleting them, preventing the skipped-word effect.
- Exposed prosody knobs in `config.json`: `sentence_silence`, `inter_chunk_pause`, `length_scale`, `noise_scale`, and `noise_w`.

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
