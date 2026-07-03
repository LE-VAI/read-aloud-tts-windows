# Changelog

## 0.4.0 - Word highlighting overlay

### Added
- Always-on-top translucent overlay that shows the text being read with the current word highlighted in real time. The overlay appears at the bottom-center of the screen and follows along as the daemon speaks.
- The daemon now writes a `highlight_state.json` file with per-word timings (approximate — distributed by character count across audio duration) at 30ms intervals during playback. The AHK overlay polls this file and moves the Edit selection to match the spoken word.
- Word timings include `[word, start_ms, end_ms]` for each token, plus the full text and total playback duration, so the overlay can pick up mid-playback without needing to catch a startup event.
- Overlay auto-hides when speech finishes or is stopped (Ctrl+Alt+Space).

### Changed
- `_synthesize_to_wav_bytes` now returns `(wav_bytes, total_samples, sample_rate)` so word timings can be computed from actual audio duration.
- `handle_speak` synthesizes all chunks up front (building word timings across the full text) before playing, then emits highlight state via a background timer thread.
- `StopSpeech` in AHK now stops the highlight timer and hides the overlay.
- `ExitFunc` cleans up the overlay on AHK exit.

## 0.3.0 - Model keep-alive daemon (near-instant first audio)

### Added
- Long-lived Piper model daemon (`speak_server.py`) that loads the ONNX voice model once on startup and serves subsequent speak requests from memory, eliminating the ~2s model-load cold start on every Ctrl+Right-click. Synthesis drops from ~1.9s (full cold start) to ~150-500ms (inference only) after the first load.
- `--serve` flag on `speak.py` to launch the daemon mode.
- File-based request/response protocol (`tmp/request.json` / `tmp/response.json`) between AutoHotkey and the daemon — no named pipes, no extra dependencies.
- Automatic daemon startup when the AHK tray launches (model pre-warms at Windows login via the startup shortcut).
- Graceful fallback: if the daemon is unavailable, the AHK automatically uses the original `speak.py --input-file` cold-start path with no regression.
- "Restart Daemon" tray menu item.
- Clean shutdown: daemon quits on AHK exit via `OnExit`.

### Changed
- AutoHotkey hotkeys are now registered before `StartDaemon()` so the keyboard hook installs before the auto-execute section blocks on daemon warmup.
- `speak_server.py` uses the `PiperVoice` Python API (`PiperVoice.load()` + `.synthesize()`) for in-memory synthesis with no subprocess and no temp WAV files — audio is played via `winsound.PlaySound` with `SND_MEMORY`.
- Sentence silence is handled by inserting silence bytes between `AudioChunk` boundaries (the `SynthesisConfig` API has no sentence_silence field, unlike the Piper CLI).

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
