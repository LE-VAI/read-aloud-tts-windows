# Changelog

## Unreleased — Stability hardening (long sessions, long text)

Fixes for stalls, leaks, and silent failures found by an adversarial
long-session / long-document audit. The headline defect: a single hung
synthesis call could permanently silence the daemon until it was restarted.

#### Fixed
- **A hung synthesis could brick every later read (critical).** Synthesis holds
  the playback lock for the whole read, and chunk 0 is synthesized while
  holding it. Neither Piper nor ONNX Runtime offers a timeout, and the
  phonemizer runs through an embedded espeak-ng — a component with documented
  indefinite hangs. One such hang held the lock forever, and because a
  takeover's `join(3)` gives up and leaves the wedged worker alive, every later
  read blocked behind it and produced no audio, with no visible error. Synthesis
  now runs under a length-aware watchdog; a wedge is abandoned, logged loudly,
  and the daemon stays usable.
- **Unbounded lock acquisition.** Even after a wedge was abandoned, the
  playback lock was acquired with no timeout, so a stuck predecessor blocked
  the next read indefinitely. Acquisition is now bounded (60s) and logs when it
  trips.
- **Unbounded takeover join.** A takeover waited 3s for the previous worker and
  then started the new read regardless — leaving the wedged worker holding the
  lock. It now grants a second, longer grace period and reports the wedge
  instead of failing silently.
- **`winsound` failures aborted the read and leaked its temp dir.** Per
  CPython docs `PlaySound` raises `RuntimeError` when the system reports an
  error — which is what a Bluetooth/USB audio-endpoint change looks like. An
  unhandled raise on the playback thread skipped the temp-dir cleanup. Play and
  cancel are now individually armoured and log the failure.
- **The keep-alive heartbeat exercised nothing.** It synthesized a single
  space, which phonemizes to a boundary-only sequence and yields **zero audio
  bytes** — so the 5s warm-up never touched the model, and a wedged session
  would have looked perfectly healthy. It now synthesizes a real word, asserts
  a nonzero result, and skips while a read is in progress (removing a
  concurrent-phonemization hazard against the same espeak-ng instance).
- **Leaked temp dirs from unclean shutdowns.** A `playback-*` directory is
  removed in a `finally`, which a crash, `taskkill`, or shutdown mid-read
  skips. Six such dirs (7 MB), dated months earlier, were still on disk in a
  live install. Startup now sweeps them, age-gated so a read in progress is
  never touched.
- **`config.json` was re-read from disk once per chunk.** It is a shared
  read/write file also touched by speed and voice changes. Reads are now served
  from a short-TTL cache that every write invalidates, so a speed change is
  still visible to the next chunk.

#### Changed
- **`JsonEscape` no longer rebuilds the string per control character.** The
  old per-character loop copied the entire selection once per control character
  found, making a Home press cost O(n × k) on PDF-style text. It is now a
  single `RegExReplace` pass: ~18× faster on a 30k selection (5.4 ms → 0.3 ms)
  and, more importantly, correct by construction — an intermediate strided
  rewrite MISSED a control character whenever the length was an exact multiple
  of the stride, which would have shipped as a silent no-read (invalid JSON,
  rejected request, no audio, no error). Verified against the old algorithm
  with an exhaustive control-code × offset grid.

#### Added
- **AutoHotkey syntax gate.** A syntax error in the tray script is a total
  outage — AHK shows a modal dialog and every hotkey dies until it is dismissed.
  `src/check_ahk_syntax.py` parses the script with `/validate` (without
  executing it, so no hotkeys register and no daemon spawns) and runs in CI and
  in the test suite. This failure has reached live use twice.
- **Stability regression tests** covering the synthesis watchdog, the heartbeat
  probe, the audio-failure armour, the temp-dir sweep, the config cache, and
  the tray script's parseability.

## Unreleased (0.9.0) — Reading panel + web overlay

### Desktop reading panel

#### Added
- **Desktop reading panel — a karaoke follow-along window.** With the
  panel enabled (tray menu → *Word highlight box*, persisted as
  `highlight_overlay`), each read opens a compact always-on-top window
  that renders the spoken text with the current word highlighted in
  amber, page-flipping as the highlight advances so the spoken line
  stays on screen.
  - **Click any word to read from there** — speech restarts from the
    clicked word; clicks that land between words snap to the nearest
    one, so the whole panel is clickable.
  - **`Space` pauses and resumes** while the pointer is over the panel
    (or the panel is focused) — deliberate and visible, with typing
    elsewhere untouched.
  - **Zoom: `Ctrl + mouse wheel` over the panel, or drag the corner
    grip** — scales the whole panel 75%–200% as one unit (text, spacing,
    and status line together), re-anchors the highlighted word, and
    keeps working mid-read. One multiplier composes with the DPI scale,
    so zoom behaves identically on any display scaling.
  - **Drag to move** — the panel position persists across reads.
  - **`Esc` dismisses** the panel for the current read; speech
    continues.
  - **↻ Replay bar** — after a finished read, a small bar offers a
    one-click re-read of the same text for 8 seconds.
- **Tray menu**: *Word highlight box* toggle and *Open Reading Overlay*
  items; `Ctrl+Alt+T` opens a scrollable transcript of the last read
  (or the clipboard when nothing was read yet).
- **Vendored read-along component** — the overlay's web component now
  ships in-repo (`src/component/`, MIT), and `install.ps1` copies it
  (plus `overlay_server.py` and `overlay.html`) into the install
  folder. The overlay works out of the box on a fresh clone; the
  `component_root` config key still points it at a local component
  checkout for development.

#### Fixed
- **Page-flip follow** — on the read-only panel, the classic scroll
  messages (EM_SCROLLCARET / EM_LINESCROLL / WM_VSCROLL) are no-ops, so
  the view froze on the first two lines while the highlight marched on
  ("dialogue frozen after the first few words"). The panel now scrolls
  with EM_SETSCROLLPOS so the highlighted line becomes the top visible
  line, with the line pitch measured live (correct at any zoom).
- **Click-to-seek robustness** — a click-to-rewind race could vanish
  the panel or flash a blue text selection; closed at both layers
  (overlay rebuild and selection handling), and gap clicks snap to
  the nearest word instead of silently doing nothing.
- **Esc dismissal sticks** — an Esc-dismissed panel no longer resurrects
  30ms later when the state tick rebuilds it.
- **Hotkey guard** — an unguarded process-name query could throw a
  modal error that killed every hotkey until restart.
- **Debug log rotation** — the debug log grew unboundedly (~1.2MB/day
  of tick lines); it now rotates at 2MB keeping one previous
  generation, with the size check amortized over writes.
- **Log flooding gated** — per-tick state lines (30/s idle) and the
  per-seek OnStart detail lines are now transition-gated.

### Web reading overlay

#### Added
- **Reading overlay — a karaoke viewer for the spoken text.** The daemon now
  serves a loopback-only page at `http://127.0.0.1:<overlay_port>/overlay`
  that renders the text being spoken with each word highlighted in place, in
  a configurable color. It replaces the fixed Edit-control text box with a
  real render: full text in place (not a copy in a small window), a
  word-by-word highlight in the color of your choice (the legacy Edit
  control's selection color was system-fixed and not recolorable), a soft
  sentence tint, and **click any word to read from there** — the page sends
  a seek back to the daemon, which restarts speech from that word, exactly
  like the tray's restart. Opening the page mid-read joins the current
  position. Speech is unaffected when the page is closed; hotkeys continue
  if the port is busy.
- Reuses the read-along web component in external-clock mode: the daemon
  owns the audio and the clock (its 30ms `highlight_state.json` writes
  become the word list and engine ticks), the page only renders. The HTTP
  layer writes the same `request.json` protocol AutoHotkey uses — no new
  daemon surface, and a once-per-speak `overlay_text.json` sidecar lets a
  page opened during a read join mid-word-list.
- New config keys: `overlay_port` (default 8792, loopback only),
  `highlight_color` (default `#FFC400`; served as plain `rgb()` — exotic
  color functions can silently drop inside `::highlight()` paint rules),
  `component_root` (the read-along component directory; a fixed whitelist
  is served from it).
- `src/overlay_mock.py` — a scripted mock daemon that drives the same state
  protocol without Piper, for UI testing and screenshots.
- `?autotest=1` self-driving verification mode: the page joins, advances,
  seeks to word 12, freezes the receipt frame, and holds the browser's
  load event until the frame is ready — so a headless screenshot
  deterministically captures the final probe line
  (`autotest started=true seeked=true paused=true tok=12 … base=12`).

#### Fixed
- **The invisible-highlight trap, caught in verification:** the overlay
  initially served the component without its `read-along.css`, so
  registered `::highlight()` ranges had no paint rule — ranges present,
  nothing painted, no error anywhere. The stylesheet link is now part of
  the served page (the component's shadow template deliberately ships no
  `::highlight()` rules; host pages must load them).

## 0.8.2 - Voice-switch reliability (2026-08-30)

### Fixed
- **Voice switching no longer breaks the app** — three stacked failure modes, all fixed:
  - **UTF-8 BOM poisoning (root cause of the 2026-08-30 total outage):** AHK v2 `FileAppend` with `"UTF-8"` encoding writes a 3-byte BOM when it *creates* a file. One tray-toggle click rewrote `config.json` with a BOM; Python's `json` rejected it and silently fell back to empty defaults — the app lost its voice list and every Home press failed silently for 20 minutes. Fixed on both sides: every AHK write now uses `"UTF-8-RAW"` (25 call sites), and `load_config()` strips a leading BOM before parsing so the shared file is robust regardless of which side wrote it last.
  - **Ghost voices in the menu:** the tray Voice menu listed every `config.json` entry, but two of five voices had never been downloaded. Picking one could never work — and the old `set_voice` persisted the selection *before* checking the files exist, poisoning `current_voice` for every later read. Now the menu lists only installed voices, and both the CLI and daemon validate model files before persisting (a missing-files voice gets a clean refusal, config untouched).
  - **UI freeze on switch:** the tray Voice handler ran a synchronous `RunWait` Python process — the whole app froze for a Python cold start on every switch. Switching now goes daemon-direct: `set_voice` request over the existing IPC, validated + persisted by the daemon, ~0.2s with a warm cache.
- **Silent failures now speak up:** `SpeakViaDaemon` never read the daemon's response, so error statuses played nothing and showed nothing. `WaitResponse` now captures the response content; speak failures surface the daemon's message as a tray tip.
- **Daemon no longer starts "ready" with zero voices:** startup now falls back to any installed voice when the configured one is unusable, and exits with a clear log line (instead of becoming a silent zombie) when no usable voice exists.
- **Config can no longer be overwritten with empty defaults:** `set_speed`/`set_voice` in the daemon refuse to persist when the loaded config has no voices (corrupt/missing file) — previously a speed tweak after a config read failure would write the empty fallback over the user's real config, permanently destroying the voice list.

### Added
- **Regression tests for the outage class** (5 new, 60 total): BOM-prefixed config parses identically to clean config, clean files pass through untouched, `voice_files_exist` detects missing/real files, and `set_voice` refuses to persist a voice with missing files.
- **Atomic config writes:** `save_config` writes to a temp file and `os.replace`s it — the AHK tray and daemon can never read a torn half-written file.
- **Bounded voice cache (max 8)** with research-backed sizing: ONNX Runtime's session destroy/create cycle is a documented RSS leak in long-running processes (microsoft/onnxruntime#26831), so the cache is deliberately sized above the full catalog — switches between loaded voices stay instant and leak-free, and the bound is only a safety valve against pathological configs.

## Unreleased - Audit fixes (0.8.1)


## Unreleased - Audit fixes (0.8.1)

### Added
- **Word-highlight overlay revived** — the pipelined-playback rewrite silently dropped the `start`/`playing` highlight-state writes, so the overlay GUI, hover-pause, and click-to-rewind never appeared (dead code since 0.7.1). The daemon now writes `{"state":"start",...}` before chunk 0 plays and `{"state":"playing","ms":...}` every ~30 ms during playback; when a later chunk finishes synthesizing mid-playback, the payload includes the grown `words` array so the overlay can highlight ahead. The AHK side re-parses timings whenever a larger words array arrives.
- **Fenced code blocks silenced** — ```` ```lang ... ``` ```` blocks are replaced with "(code block)" instead of reading source symbols aloud.
- **HTML entity decoding** — clipboard copies from web pages carrying `&amp;`, `&lt;`, `&#39;`, `&mdash;`, etc. (plus numeric entities) are decoded before synthesis instead of being read literally ("amp semi").
- **Bare URLs simplified** — `https://github.com/user/repo` now reads as "github.com" (scheme, path, credentials, port, and `www.` stripped) instead of character-by-character spelling.
- **Sentence-boundary truncation** — text over `max_chars` is cut at the last sentence end within budget (word-boundary fallback), not mid-word.

### Fixed
- **Tray "Stop" tooltip** said `Ctrl+Alt+Space`; the actual stop hotkey has been `F6` for several releases.
- **Transcript window scrollbar** was explicitly disabled (`-VScroll`) despite the control being documented as scrollable; long transcripts can now be scrolled (`+VScroll`).
- Numbered-list ordinals extended from 10 to 20 (Eleventh…Twentieth); beyond that falls back to "Number N,".

## Unreleased - Naturality pass

### Added
- **Markdown-to-speech normalizer** (`normalize_markdown()` in `speak.py`) — converts markdown formatting to speech-friendly flowing text before Piper sees it. Piper (VITS) synthesizes each sentence in isolation, so markdown tables produce isolated cell fragments with 0.4s gaps between them — "Cause" [gap] "Fix" [gap] "value..." — jumpy and unnatural. The normalizer:
  - **Tables → flowing prose**: `| Cause | Fix |` rows become "Table with columns: Cause, Fix. Cause: no trailing silence. Fix: append 0.3s." — gives Piper full sentence context with column headers as anchors.
  - **Bold/italic stripped**: `**bold**` and `*italic*` → plain text (espeak-ng can't parse markdown emphasis).
  - **Inline code stripped**: `` `code` `` → plain text.
  - **Links → text**: `[text](url)` → just the link text.
  - **Headings stripped**: `# Heading` → `Heading` (hashes confuse espeak-ng).
  - **Bullets stripped**: `- item` → `item`.
  - **Numbered lists → ordinals**: `1.` → `First,`, `2.` → `Second,`, etc. (more natural than "one dot").
  - **Blockquotes stripped**: `> quote` → `quote`.
  - **Horizontal rules removed**: `---` lines deleted (they create pauses without content).
- **Tests for markdown normalization** — 16 new tests in `test_speak.py` covering table conversion, bold/italic/code stripping, headings, links, bullets, numbered lists, blockquotes, mixed content, and integration with `normalize_text()`.

### Changed
- **Prosody parameters tuned for naturality** — three parameters adjusted based on research into VITS stochastic duration predictor behavior:
  - `noise_w` 0.8 → 0.3 — controls phoneme duration variation. At 0.8, the VITS stochastic duration predictor randomly stretches phonemes, creating perceived pauses at arbitrary points within phrases (e.g., "all... but the last" instead of "all but the last"). At 0.3, durations are more uniform, eliminating spurious mid-phrase pauses. Below 0.3 starts to sound robotic.
  - `noise_scale` 0.667 → 0.4 — controls pitch/prosody randomness. At 0.667, random pitch inflections ("weird inflections") occur. At 0.4, pitch is more controlled while still natural. Below 0.3 is too monotone.
  - `sentence_silence` 0.75 → 0.4 — the inter-sentence gap. 0.75s was unusually high (Piper default is 0.2s); it created long gaps after every sentence, contributing to the "jumpy" feel when reading structured content. 0.4s is closer to natural speech pauses.
  - `inter_chunk_pause` 0.3 → 0.25 — slightly tighter chunk transitions.
- **Version bumped** to 0.8.0.

## Unreleased - Quality and stability pass

### Added
- **On-the-fly speed control** — adjust reading speed without restarting the daemon or reloading the voice model:
  - `Ctrl+*` (`Ctrl+Shift+8`) = 10% faster (`*` = multiply = more speed, easy to remember)
  - `Ctrl+/` = 10% slower (`/` = divide = less speed, easy to remember)
  - Tray menu "Speed:" item shows current speed and cycles through presets (normal → 1.2× slower → 1.5× slower → 0.8× faster) to reset
  - Avoids zoom conflict (`Ctrl+/-`) and works on compact keyboards (Logitech MX Keys) without Alt or numpad — `*` is `Shift+8`, `/` is its own key
  - Speed changes take effect on the **next chunk** being synthesized (standard TTS behavior — audio already playing is not affected)
  - Persists to `config.json` (`length_scale` key), survives restarts
  - Range: 0.5 (2× faster) to 2.0 (2× slower), clamped to prevent artifacts at extremes
  - `handle_set_speed()` in `speak_server.py` sets a `_runtime_length_scale` override that `synth_chunk()` picks up per-chunk (rebuilds `SynthesisConfig` each call). Piper's `length_scale` is a per-call ONNX input, not a model property — zero cost to change between calls.
- **Fast-start first chunk** — `first_chunk_chars` config option (default 150) caps the first chunk at a small size so audio starts in 1-2s instead of 6-13s. Chunk 0 is synthesized synchronously before playback begins, so a full 600-char chunk at `length_scale=1.2` meant 6-13s of silence before first audio. Subsequent chunks pipeline (synth N+1 while N plays) so they remain full-size (600 chars). `chunk_text()` in `speak.py` gained a `first_chunk_chars` parameter.
- **New voices** — `en_US-libritts_r-medium` (LibriTTS-R audiobook corpus, paragraph-aware prosody, 904-speaker model) and `en_US-lessac-high` (higher-quality Lessac, smoother sustained vowels, reduced VITS shimmer, ~114MB). Both available in config and tray menu voice switcher.
- **CI workflow** (`.github/workflows/ci.yml`): runs pytest, py_compile, sanitize-check, and smoke-test on every push and pull request (windows-latest).
- **Issue templates**: bug report and feature request templates with environment fields.
- **`.gitattributes`**: line-ending normalization — CRLF for `.ps1`/`.ahk`/`.cmd`, LF for `.py`/`.json`/`.md`, binary for assets.
- **Keep-alive heartbeat thread** in `speak_server.py`: synthesizes a single space every 5s and discards the audio, keeping the ONNX Runtime session warm during long idle stretches. Belt-and-suspenders against microsoft/onnxruntime#7449 (ORT intra-op thread-pool parking after idle). `_heartbeat_stop` Event wired into the quit and KeyboardInterrupt paths so the thread exits cleanly.
- **`DebugLog()` helper** in `ReadAloudTTS.ahk`: appends timestamped lines to `tmp/working_debug.log`. Called at `ReadSelection()` entry to confirm the hotkey actually fires (diagnoses Electron-vs-AHK hook races).

### Fixed
- **Intermittent last-word truncation** — three compounding causes fixed:
  1. **Trailing silence appended** to PCM after the last sentence (0.3s). Piper by design adds no silence after the final sentence of a synthesis call (per `--sentence-silence` docs: "all but the last"). Without a tail, audio ends abruptly at the last phoneme and any timing slop cancels the word mid-phoneme.
  2. **`PlaySound(None, 0)` no longer called unconditionally** — only fires when the user requests stop. Previously it fired after every poll-timer expiry, truncating audio still playing due to variable WASAPI device-open latency (~500ms on Realtek/Intel hardware — see Microsoft Q&A 1168479). The 0.5s buffer was the same order of magnitude as device startup, explaining the "sometimes randomly" intermittency.
  3. **Poll buffer increased** from 0.5s to 0.8s (0.5s device-open margin + 0.3s trailing silence margin).
- **Config corruption resilience** — `load_config()` in `speak.py` now catches `JSONDecodeError`, `FileNotFoundError`, and `OSError`, falling back to `_default_config()` defaults instead of crashing the daemon. Logs a warning so the user knows config was reset.

### Changed
- **Upgraded piper-tts 1.4.2 → 1.6.0** — gains default speaker id for multi-speaker voices (needed for libritts_r), bumped embedded espeak-ng, fixed pathvalidate dependency, added Hebrew phonemizer. No breaking Python API changes.
- **Smoke test** now validates `speak_server.py` syntax (previously only `speak.py`) and requires `.gitattributes`.
- **CONTRIBUTING.md** pre-PR checklist updated to run pytest on both test suites instead of just `py_compile` on `speak.py`.
- **`speak.py --serve`** path simplified: removed redundant piper pre-flight (now handled by `speak_server.serve()` via `_ensure_piper()`).
- **Hotkeys now use `$*` prefix** in `ReadAloudTTS.ahk`: `$` forces AHK's low-level keyboard hook (instead of RegisterHotkey, which Electron apps like ZCode/VS Code override at the window-proc level — Home was eaten before AHK saw it), `*` fires regardless of modifier state. Fixes "TTS works in browser but not in ZCode."
- **Tuned ONNX Runtime session** in `speak_server.py _load_voice()`: replaces the bare default `InferenceSession` that `PiperVoice.load()` creates with one carrying `intra_op_num_threads=1`, `graph_optimization_level=ORT_ENABLE_ALL`, `allow_spinning=1`, `spin_duration_us=1000`, `spin_backoff_max=8`. Per ORT maintainer tlh20 in microsoft/onnxruntime#7449, `intra_op_num_threads=1` eliminates the intra-op thread pool entirely (no worker threads to park = no wake cost). Verified over 3 days: warm/recent-read latency dropped from 8-13s to sub-second (0.1-0.9s); long-idle (hours) latency dropped from 12-13s to 4-7s (residual is CPU C-states, not ORT).
- **Naturalness defaults introduced**: `sentence_silence` 0.5 → 0.75s, `length_scale` 1.0 → 1.2 (narration pace). Both are config values, instantly adjustable via speed hotkeys. (Note: `sentence_silence` was later reduced to 0.4s in the naturality pass — see above.)

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

## Unreleased - Launch polish

### Added
- **App icon everywhere** — multi-size `app.ico` (16/24/32/48/64/128/256) generated from the logo; tray icon branded via `TraySetIcon` (was AutoHotkey's generic icon), tray hover tooltip ("ReadAloudTTS — select text, press Home"), and the Startup shortcut gets `IconLocation`. `install.ps1` copies the icon into the install dir automatically.
- **README badges** — CI status, latest release, and license badges above the fold.
