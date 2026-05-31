# Notices

ReadAloudTTS is distributed as source code under the MIT License.

This repository does not bundle AutoHotkey, Piper binaries, Piper voice models, downloaded voice config files, generated WAV files, logs, or virtual environments.

## Third-party components

### AutoHotkey v2

AutoHotkey is used to run the Windows tray helper and hotkeys.

- Project: https://github.com/AutoHotkey/AutoHotkey
- License: GPL-2.0 for AutoHotkey itself.
- Distribution note: this repository does not bundle AutoHotkey binaries.

### Piper / piper-tts

Piper is used for local text-to-speech synthesis.

- Project: https://github.com/rhasspy/piper
- Package: `piper-tts`
- License note: the archived Piper repository includes an MIT license file.
- Distribution note: this repository installs Piper into the user's local virtual environment and does not bundle Piper binaries.

### Piper voice models

Voice models are downloaded separately by the user. Each voice can have its own model card, dataset source, and license terms.

- Voice source: https://huggingface.co/rhasspy/piper-voices
- Distribution note: this repository does not bundle ONNX voice files or downloaded voice JSON files.
- Review `docs/VOICE_LICENSING.md` before using any voice for commercial or public work.
