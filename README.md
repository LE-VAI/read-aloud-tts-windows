# ReadAloudTTS for Windows

![ReadAloudTTS hero](docs/assets/readme-hero.png)

Offline Windows selected-text read-aloud helper using AutoHotkey and Piper TTS. Built for local utility: **select text, press `Home`, and hear it spoken instantly** — without sending a word to a cloud service.

ReadAloudTTS is source-only, privacy-first, and designed to stay out of the way. One key to read, one key to stop. It is not a browser extension and not a cloud reader.

![ReadAloudTTS feature strip](docs/assets/feature-strip.png)

## The one-second pitch

Select any text. Press **`Home`**. It speaks. Press **`F8`** to stop. That's the whole interaction — no menus, no dialogs, no cloud round-trip. The Piper voice model is loaded once at startup and stays warm, so the first read is the only one with a brief model-load delay. Every read after that is near-instant.

## Hotkeys

ReadAloudTTS ships with several hotkeys so you can pick whichever fits your workflow. All of them do the same thing — read the current text selection (or stop the speech in progress).

| Action | Hotkey | Notes |
| --- | --- | --- |
| **Read selection** | `Home` | Single key. Fastest for daily use. |
| Read selection | `F6` or `F12` | Function-key alternatives if `Home` conflicts with an app. |
| Read selection | `Ctrl + Right-click` | The original gesture. Leaves normal right-click menus intact. |
| **Stop speech** | `F8` | Single key. Mirrors the single-key read convenience. |
| Stop speech | `Ctrl + Alt + Space` | The original two-handed stop gesture. |
| Show transcript | `Ctrl + Alt + T` | Opens the recent-speech transcript. |

Hotkeys are plain AutoHotkey v2 bindings near the top of `ReadAloudTTS.ahk`. Remap any of them to your own keys in seconds — see [Remapping hotkeys](#remapping-hotkeys) below.

## Highlights

| Capability | Detail |
| --- | --- |
| Offline speech | Uses locally installed Piper after voice download. |
| Single-key read | Press `Home` on any selection to hear it instantly. No chord, no menu. |
| Single-key stop | Press `F8` to cancel speech the moment you've heard enough. |
| Clipboard care | Temporarily copies selection, then restores your previous clipboard contents. |
| Natural cadence | Configurable sentence pauses, inter-chunk gaps, and Piper prosody knobs. |
| Pipelined playback | Chunk N+1 is synthesized while chunk N plays, minimizing gaps. |
| Source-only | No bundled voice models, logs, generated WAVs, or binaries. |

## What it does

- Reads selected text from any app that supports normal copy.
- Offers single-key hotkeys (`Home`, `F6`, `F12`) for read and `F8` for stop, plus the original `Ctrl + Right-click` / `Ctrl + Alt + Space` gestures.
- Restores your previous clipboard contents after temporarily copying the selected text.
- Runs from the Windows tray with actions for reading, stopping, voice selection, config, and logs.
- Uses Piper TTS locally after voices are downloaded.

## Privacy

Selected text is briefly copied to the clipboard so the helper can read it. The previous clipboard contents are restored immediately after the selection is captured.

This tool does not send selected text to a cloud service. Piper speech synthesis runs locally after setup. Voice downloads require network access during setup or when you run `download_voices.ps1`.

Clipboard managers, endpoint tools, or apps with clipboard monitoring may observe the temporary clipboard change. Do not use this helper with text you do not want any local clipboard tool to see.

## Supported platform

Windows only.

## Prerequisites

- PowerShell.
- Python 3.12 or newer preferred.
- `winget` recommended for AutoHotkey installation.
- AutoHotkey v2.
- Piper TTS, installed into the app virtual environment by `install.ps1`.

## Quick install

Open PowerShell in the repository folder and run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

The installer uses `%LOCALAPPDATA%\ReadAloudTTS` by default, creates a Python virtual environment, installs Piper TTS, copies the source files, creates `config.json` from `config.example.json`, creates a startup shortcut, and offers to download a voice.

To skip voice download during install:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -SkipVoiceDownload
```

You can download voices later:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\download_voices.ps1
```

## Quick uninstall

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1
```

By default, uninstall removes the startup shortcut and stops the helper. It asks before removing the installed app folder. To remove it without a prompt:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\uninstall.ps1 -RemoveAppData -Force
```

## Usage

1. Select text in any app that supports copy.
2. Press `Home` (or `F6` / `F12` / `Ctrl + Right-click`).
3. Press `F8` (or `Ctrl + Alt + Space`) to stop speech.
4. Use the tray menu to read, stop, change voice, open config, or open logs.

## Tuning speech cadence

After install, `config.json` (in the app folder, copied from `config.example.json`) includes optional keys that control how Piper speaks. Edit the file and restart the helper for changes to take effect.

| Key | Default | Effect |
| --- | --- | --- |
| `chunk_chars` | `2000` | Maximum characters per Piper invocation. Lower values = more chunks (more cold starts); higher = fewer chunks (faster). |
| `sentence_silence` | `0.5` | Seconds of silence Piper inserts after each sentence. Increase for more breathing room; decrease for faster delivery. |
| `inter_chunk_pause` | `0.3` | Seconds of silence between chunk playbacks. Prevents abrupt merges at chunk boundaries. |
| `length_scale` | `1.0` | Overall speech speed. `1.0` = natural, `0.8` = slower/clearer, `1.2` = faster. |
| `noise_scale` | `0.667` | Pitch/prosody variation. Higher = more expressive intonation; lower = flatter. |
| `noise_w` | `0.8` | Phoneme duration jitter. Higher = more human-like rhythmic irregularity; lower = steadier cadence. |

If a key is absent from `config.json`, the voice model's built-in defaults are used.

## Why multiple hotkeys

Different workflows suit different keys. `Home` is the fastest daily driver — one tap on any selection and it speaks. `F6` and `F12` are there for apps that hijack `Home` (some terminals and editors do). `Ctrl + Right-click` is kept as the original gesture because Windows apps implement context menus differently and injecting a custom item into every app's right-click menu would be fragile and invasive; `Ctrl + Right-click` gives a consistent gesture while leaving the normal right-click menu alone.

## Remapping hotkeys

Hotkeys are plain AutoHotkey v2 bindings near the top of `ReadAloudTTS.ahk` (look for the `::` lines). To remap, change the left side of any binding to your preferred key. For example, to read with `CapsLock` instead of `Home`:

```autohotkey
; In ReadAloudTTS.ahk, replace:
Home::ReadSelection()
; with:
CapsLock::ReadSelection()
```

See the [AutoHotkey v2 hotkey documentation](https://www.autohotkey.com/docs/v2/Hotkeys.htm) for the full key name list and modifier syntax. Restart the helper after editing.

## Limitations

- Works only where selected text can be copied.
- Some apps block copy or custom selection access.
- Clipboard managers may observe temporary clipboard changes.
- Voice downloads require network access during setup.
- Voice model licenses vary by voice and must be reviewed before commercial or public use.

## Safety notes

- Do not commit `config.json`, downloaded voices, generated WAV files, logs, temp files, or virtual environments.
- Run the sanitization check before any commit or publication.
- This repository is source-only and does not bundle AutoHotkey, Piper binaries, or Piper voice models.

## Repo visuals

Visual assets live in `docs/assets/`:

- `readme-hero.png`
- `social-preview.png`
- `logo-mark.png`
- `feature-strip.png`

The GitHub social preview image is `docs/assets/social-preview.png`.

## Voice licensing

Read `docs/VOICE_LICENSING.md` before downloading or using voices, especially for commercial or public work. The `hfc_female` voice is flagged as non-commercial/share-alike sensitive because its model card references CC BY-NC-SA 4.0 dataset licensing.

## Troubleshooting

See `docs/TROUBLESHOOTING.md`.
