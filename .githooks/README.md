# Git hooks

This repo ships a pre-commit hook that runs the sanitization gate
(`scripts/sanitize-check.ps1`) before every commit. Enable it once per clone:

    git config core.hooksPath .githooks
