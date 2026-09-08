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

## The reading panel

While speech plays, ReadAloudTTS can show a compact always-on-top **reading panel**: the text being read with the current word highlighted, following the speech word by word.

Enable it with the tray menu item **Word highlight box** (the choice is remembered in `config.json` as `highlight_overlay`).

Once it is showing:

- **Click any word** to restart speech from that word. Clicks between words snap to the nearest word.
- **`Space`** pauses and resumes — only while the pointer is over the panel, so typing elsewhere is unaffected.
- **`Ctrl + mouse wheel`** over the panel, or **dragging the corner grip**, zooms it between 75% and 200%. Reading continues; the highlighted word stays anchored.
- **Drag the panel** by its top area to move it anywhere — the position is remembered.
- **`Esc`** dismisses the panel for the current read; speech continues.

When a read finishes, a small **↻ Replay** bar appears for 8 seconds — click it to re-read the same text from the start.

## The web overlay

The daemon also serves a karaoke viewer page at `http://127.0.0.1:8792/overlay` (loopback only, never exposed to the network). It renders the full spoken text in place with each word highlighted as it is spoken; clicking a word reads from there, and the page can be opened mid-read to join the current position. See the README's *Reading overlay* section for details and the `overlay_port` / `highlight_color` config keys.

## Tray menu

The tray menu includes:

- `Read Selection`.
- `Stop`.
- `Speed` (shows the current speed; click to cycle presets back to normal).
- `Word highlight box` (toggles the reading panel on/off).
- `Voice`.
- `Open Reading Overlay` (opens the web overlay page).
- `Open Config`.
- `Open Logs`.
- `Show Transcript` (`Ctrl+Alt+T`).
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
- `overlay_port`: port for the loopback karaoke viewer page (default 8792).
- `highlight_color`: hex color for the active-word highlight (default `#FFC400`).
- `highlight_overlay`: whether the reading panel shows on reads (default off).
- `component_root`: where the overlay serves the read-along component from (defaults to the bundled copy in the install folder).
- `voices`: voice labels and local model paths.
