# Changelog

## Unreleased - Repo hardening

### Added
- **On-the-fly speed control** — adjust reading speed without restarting the daemon or reloading the voice model:
  - `Ctrl+Alt+]` = 10% faster (borrows the VLC/mpv bracket-key convention)
  - `Ctrl+Alt+[` = 10% slower
  - `Ctrl+Alt+\` = reset to normal (1.0×)
  - Uses `Ctrl+Alt+` prefix (same as `Ctrl+Alt+T` for transcript) to avoid conflicts with zoom (`Ctrl+/-`), browser tab-switching (`Ctrl+digit`), and text selection (`Shift+Home`). Per research: Thorium Reader solved the same zoom conflict with `Ctrl+5/6/7`, but that conflicts with browser tabs in a global-overlay app. `Ctrl+Alt+bracket` is essentially unbound in all reading apps.
  - Tray menu "Speed:" item shows current speed and cycles through presets (normal → 1.2× slower → 1.5× slower → 0.8× faster)
  - Speed changes take effect on the **next chunk** being synthesized (standard TTS behavior — audio already playing is not affected)
  - Persists to `config.json` (`length_scale` key), survives restarts
  - Range: 0.5 (2× faster) to 2.0 (2× slower), clamped to prevent artifacts at extremes
  - `handle_set_speed()` in `speak_server.py` sets a `_runtime_length_scale` override that `synth_chunk()` picks up per-chunk (rebuilds `SynthesisConfig` each call). Piper's `length_scale` is a per-call ONNX input, not a model property — zero cost to change between calls.
- **Fast-start first chunk** — `first_chunk_chars` config option (default 150) caps the first chunk at a small size so audio starts in 1-2s instead of 6-13s. Chunk 0 is synthesized synchronously before playback begins, so a full 600-char chunk at `length_scale=1.2` meant 6-13s of silence before first audio. Subsequent chunks pipeline (synth N+1 while N plays) so they remain full-size (600 chars). `chunk_text()` in `speak.py` gained a `first_chunk_chars` parameter.
- **CI workflow** (`.github/workflows/ci.yml`): runs pytest, py_compile, sanitize-check, and smoke-test on every push and pull request (windows-latest).
- **Issue templates**: bug report and feature request templates with environment fields.
- **`.gitattributes`**: line-ending normalization — CRLF for `.ps1`/`.ahk`/`.cmd`, LF for `.py`/`.json`/`.md`, binary for assets.
- **Keep-alive heartbeat thread** in `speak_server.py`: synthesizes a single space every 5s and discards the audio, keeping the ONNX Runtime session warm during long idle stretches. Belt-and-suspenders against microsoft/onnxruntime#7449 (ORT intra-op thread-pool parking after idle). `_heartbeat_stop` Event wired into the quit and KeyboardInterrupt paths so the thread exits cleanly.
- **`DebugLog()` helper** in `ReadAloudTTS.ahk`: appends timestamped lines to `tmp/working_debug.log`. Called at `ReadSelection()` entry to confirm the hotkey actually fires (diagnoses Electron-vs-AHK hook races).

### Changed
- **Smoke test** now validates `speak_server.py` syntax (previously only `speak.py`) and requires `.gitattributes`.
- **CONTRIBUTING.md** pre-PR checklist updated to run pytest on both test suites instead of just `py_compile` on `speak.py`.
- **`speak.py --serve`** path simplified: removed redundant piper pre-flight (now handled by `speak_server.serve()` via `_ensure_piper()`).
- **Hotkeys now use `$*` prefix** in `ReadAloudTTS.ahk`: `$` forces AHK's low-level keyboard hook (instead of RegisterHotkey, which Electron apps like ZCode/VS Code override at the window-proc level — Home was eaten before AHK saw it), `*` fires regardless of modifier state. Fixes "TTS works in browser but not in ZCode."
- **Tuned ONNX Runtime session** in `speak_server.py _load_voice()`: replaces the bare default `InferenceSession` that `PiperVoice.load()` creates with one carrying `intra_op_num_threads=1`, `graph_optimization_level=ORT_ENABLE_ALL`, `allow_spinning=1`, `spin_duration_us=1000`, `spin_backoff_max=8`. Per ORT maintainer tlh20 in microsoft/onnxruntime#7449, `intra_op_num_threads=1` eliminates the intra-op thread pool entirely (no worker threads to park = no wake cost). Verified over 3 days: warm/recent-read latency dropped from 8-13s to sub-second (0.1-0.9s); long-idle (hours) latency dropped from 12-13s to 4-7s (residual is CPU C-states, not ORT).
- **Naturalness defaults tuned**: `sentence_silence` 0.5 → 0.75s (longer pauses between sentences — community consensus for narration), `length_scale` 1.0 → 1.2 (narration pace instead of voice-assistant pace). Both are config values, instantly adjustable via speed hotkeys.

## 0.7.2 - Playback truncation fix and AI-tool copy compatibility

### Fixed
- **Last word of a paragraph was sometimes cut off.** The playback completion poll in `speak_server.py` used a 0.15s buffer after the chunk duration — not enough for audio devices with 50-200ms startup latency. `PlaySound(None, 0)` cancelled audio before the final word finished. Buffer increased to 0.5s.
- **Pressing `Home` on selected text in AI coding tools (ZCode, etc.) stopped the agent's generation.** The AHK overlay used `Send ^c` (Ctrl+C) to copy the selection, which AI tools intercept as "stop generation" during streaming/thinking. Changed to `Send {Ctrl down}{Insert}{Ctrl up}` (Ctrl+Insert — the Windows legacy copy shortcut, respected by Chrome/Electron/text editors but not bound to "stop" by AI tools).

### Changed
- **Documentation aligned with 0.7 hotkeys.** USAGE.md, PRIVACY.md, and install.ps1 final message updated from stale `Ctrl+Alt+Space` / `Ctrl+Right-click` references to current `Home` (read) / `F6` (stop).
- **README `chunk_chars` table corrected** from 2000 to 600 (matching config.example.json and the 0.7.1 change).
- **USAGE.md config section expanded** with all prosody knobs (sentence_silence, inter_chunk_pause, length_scale, noise_scale, noise_w) and the current default values.
- **USAGE.md tray menu list updated** to include Show Transcript and Restart Daemon (added in 0.6.0 and 0.7.0 but never documented).

## 0.7.1 - Pipelined synthesis (12x faster first audio)

### Fixed
- **Critical: 12-53 second silence before first audio on multi-paragraph text.** The daemon's `handle_speak` synthesized ALL chunks to completion before playing even the first one. For a 2700-character selection (5 chunks) that meant 22-27s of silence before the user heard a single word. Longer texts waited 53s+. The original `speak.py` cold-start path pipelined (synth chunk N+1 while chunk N plays), but that optimization was lost when the daemon was built. **Now restored**: chunk 0 synthesizes synchronously, playback starts immediately, then chunk N+1 synthesizes in a background thread while chunk N plays.
- Verified: 2700-char text went from **22-27s → 1.9s** time-to-first-audio (12x improvement).

### Changed
- **`chunk_chars` reduced from 2000 → 600** (`config.example.json`): smaller first chunk = faster first word. With 600 chars, chunk 0 synthesizes in ~1-2s instead of ~7s. Subsequent chunks stream behind it seamlessly with no audible gap.
- **New log line `First audio after Xs`** in `speak_server.py`: the daemon now logs the exact time-to-first-audio so startup lag is measurable in production, not just the total synthesis time.

## 0.7.0 - Daemon self-healing and one-click refresh

### Added
- **`refresh-readaloud.cmd` / `refresh-readaloud.ps1`**: one-click user-facing recovery helper. Sends a graceful quit to any stuck daemon (works regardless of process elevation, unlike `taskkill`), waits for the single-instance mutex to release, launches a fresh daemon, and verifies readiness. Safe to run at any time, even when the daemon is already healthy. Installed alongside the other scripts by `install.ps1`.
- **`PruneStaleDaemon()`** in `ReadAloudTTS.ahk`: on startup, AHK now pings the daemon before pruning the readiness marker. If the daemon responds, the marker is kept (the daemon is alive). Only if the ping times out is the marker removed. This replaces the old blind `FileDelete` that could orphan a live daemon.
- **Orphan recovery in `StartDaemon()`**: if a fresh daemon spawn fails to produce a readiness marker within 10s (because a previous daemon still holds the mutex), AHK sends a `quit` to the orphan, waits for the mutex to release, and retries the spawn once — automatically, with a tray notification.
- **`SendDaemonQuit()`** helper in `ReadAloudTTS.ahk`: centralised the quit-IPC logic so `StopDaemon`, `StartDaemon` orphan recovery, and `PruneStaleDaemon` all share it.

### Changed
- **`speak_server.py` self-heals its readiness marker**: the daemon's main loop now checks every 20ms whether `tmp/daemon_ready` exists and recreates it if missing. This prevents the overnight desync where AHK loses track of a live daemon after the marker is deleted (by an AHK restart, cleanup script, or transient FS issue). The daemon is always "ready" while it's alive, so the marker now reflects that truth.
- **`StopDaemon()` always sends a quit request** (previously only sent one if the marker existed). The quit IPC works across process elevation; the PID force-kill remains as a fallback.
- **`RestartDaemon` tray item** now mentions `refresh-readaloud.cmd` in its failure toast.
- **`install.ps1`** now deploys `refresh-readaloud.ps1` and `refresh-readaloud.cmd` to the install folder.
- **`smoke-test.ps1`** validates the new refresh files exist and the PowerShell script parses cleanly.
- **`docs/TROUBLESHOOTING.md`** documents the "Home does nothing" symptom and the refresh helper.

### Root cause background
The overnight issue: AHK deleted `daemon_ready` on every startup (even when the daemon was alive), then spawned a competitor that exited "already running" — leaving no marker and a live-but-orphaned daemon. Home silently did nothing because AHK thought the daemon wasn't ready. The self-healing marker (daemon side) and the ping-before-prune + orphan-recovery (AHK side) together close this gap. The refresh helper is the user-facing safety net.

## 0.6.0 - Transcript overlay

### Added
- **Transcript overlay** (Ctrl+Alt+T or tray menu "Show Transcript"): opens a separate, larger, scrollable, always-on-top window showing the full text being read. Uses the last-read text if available, otherwise falls back to the clipboard. Stays open until closed so the user can review what was read.
- Resizable transcript window with a Close button and proper resize handling.
- Transcript window is cleaned up on AHK exit.

### Changed
- `HighlightFullText` global is now populated during playback and reused by the transcript overlay.
- `ExitFunc` closes the transcript window on exit.

## 0.5.0 - Hover-pause and click-to-rewind overlay

### Added
- **Hover-pause**: Moving the mouse over the highlight overlay pauses speech. Moving the mouse away resumes from the current word. The daemon re-synthesizes from the current word position on resume.
- **Click-to-rewind**: Click any word in the overlay to restart speech from that word. The overlay sends a `from_word` seek request to the daemon, which skips earlier words and re-synthesizes from the clicked position.
- `from_word` parameter on the `speak` action in the daemon protocol — skips the first N words of the text before synthesizing.
- `SeekFromWord` AHK function that sends a seek request with the original full text + word index.
- Mouse-leave detection in the highlight timer — checks if the cursor has left the overlay window and triggers resume.

### Changed
- `handle_speak` now accepts an optional `from_word` parameter that truncates the text before chunking/synthesis.
- `HighlightOnPlaying` tracks `HighlightCurrentIdx` so resume knows which word to restart from.
- `HighlightTick` checks mouse position over the overlay to detect hover-pause/resume transitions.
- `HideHighlightOverlay` resets `HighlightPaused` and `HighlightCurrentIdx`.

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
