# Abandoned Enhancement Attempt — 2026-07-02

These files are from a prior session that attempted to replace the simple
`speak.py` subprocess approach with a persistent `tts_service.py` named-pipe
service architecture. The attempt introduced multiple compounding bugs
(hung AHK auto-execute, UTF-16/byte-count mismatch in pipe writes, undefined
functions, wrong-interpreter Piper invocation) and the tool never worked
with this architecture.

The files are preserved here for reference only. They are **not used** by the
live tool and are **not installed** by `install.ps1`. Do not revive them
without understanding why they failed — see
`D:\1ATLAS\outputs\docs\2026-07-03_read-aloud-tts-restoration.md` for the
full post-mortem.

The working tool uses `src/speak.py` + `src/ReadAloudTTS.ahk` (the original
simple subprocess approach, restored from git commit 49af737).