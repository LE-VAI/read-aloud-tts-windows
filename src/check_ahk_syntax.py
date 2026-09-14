#!/usr/bin/env python3
"""Syntax-gate for src/ReadAloudTTS.ahk.

A syntax error in the shipped tray script is a TOTAL OUTAGE: AutoHotkey shows a
modal error dialog and every hotkey dies until it is dismissed. This has
happened twice in live use. Nothing in the repo checked the .ahk before commit —
this closes that gap.

`/validate` parses the whole script WITHOUT executing it, so hotkeys are never
registered, no daemon is spawned, and a running tray instance is unaffected.

Usage:  python check_ahk_syntax.py [path]
Exit:   0 = parses, 1 = syntax error, 2 = interpreter not found / could not run
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent / "ReadAloudTTS.ahk"

# Discovered at runtime — never hardcoded. Order: PATH, the standard install
# locations relative to each environment root, then the per-user install.
_ENV_ROOTS = [
    os.environ.get("ProgramFiles"),
    os.environ.get("ProgramFiles(x86)"),
    os.environ.get("LOCALAPPDATA"),
]
_RELATIVE = [
    Path("AutoHotkey") / "v2" / "AutoHotkey64.exe",
    Path("AutoHotkey") / "AutoHotkey64.exe",
    Path("Programs") / "AutoHotkey" / "v2" / "AutoHotkey64.exe",
]


def find_ahk() -> Path | None:
    """Locate the AutoHotkey v2 interpreter without hardcoding a user path."""
    for name in ("AutoHotkey64.exe", "AutoHotkey32.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for root in _ENV_ROOTS:
        if not root:
            continue
        for rel in _RELATIVE:
            candidate = Path(root) / rel
            if candidate.is_file():
                return candidate
    return None


def main(target: Path | None = None) -> int:
    """Validate `target` (default: the tray script). Returns an exit code.

    Argument handling is deliberately narrow, because this function is called
    BOTH from the command line and from the test suite. Under pytest, sys.argv
    holds the collected test files — an argv-driven default therefore pointed
    the gate at `test_speak.py` and reported a bogus parse failure against it.
    So: an explicit argument wins, and a positional argument is only honoured
    when it actually names an .ahk file. Everything else falls back to the tray
    script this gate exists to protect.
    """
    if target is None:
        candidates = [
            a for a in sys.argv[1:]
            if not a.startswith("-") and a.lower().endswith(".ahk")
        ]
        target = Path(candidates[0]).resolve() if candidates else DEFAULT
    if not target.is_file():
        print(f"target not found: {target}")
        return 2

    ahk = find_ahk()
    if ahk is None:
        # Not a failure: the gate is about catching a broken script, and it
        # cannot do that without an interpreter. Skipping loudly beats failing
        # a build for a missing tool — but it must never look like a pass, or a
        # CI runner without AutoHotkey would silently stop gating the .ahk.
        print("SKIP — AutoHotkey v2 interpreter not found on this machine; "
              ".ahk syntax was NOT checked")
        return 2

    print(f"AHK SYNTAX GATE — {target.name}")
    # /ErrorStdOut sends the parse error to stderr AND makes the process exit
    # instead of showing a modal and waiting — that is what makes this
    # scriptable. /validate alone leaves a window open.
    try:
        proc = subprocess.run(
            [str(ahk), "/ErrorStdOut", "/validate", str(target)],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  could not run validator: {e}")
        return 2

    err = (proc.stderr or "").strip()
    out = (proc.stdout or "").strip()

    if proc.returncode == 0 and not err:
        print(f"  PASS — {target.name} parses cleanly")
        return 0

    print(f"  FAIL — exit {proc.returncode}")
    for line in (err or out).splitlines():
        print(f"    {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
