# Security Policy

## Reporting a vulnerability

Please report suspected vulnerabilities through the repository's private security reporting channel when available. If private reporting is not available, open a minimal public issue that describes the affected behavior without including secrets, private text, logs, voice files, or local machine details.

## Sensitive data

Do not include any of the following in issues, pull requests, screenshots, or logs:

- Selected text that is private.
- Credentials, tokens, API keys, recovery codes, wallet material, or financial records.
- Local machine names, usernames, or absolute local paths.
- Downloaded voice models or generated audio files.
- Logs from a machine that may contain private text or paths.

## Local execution model

ReadAloudTTS runs locally on Windows. It temporarily uses the clipboard to capture selected text, restores the previous clipboard contents, and sends text only to the locally installed Piper command.
