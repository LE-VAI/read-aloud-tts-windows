import argparse
import ctypes
import json
import logging
import re
import subprocess
import sys
import tempfile
import textwrap
import winsound
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "logs" / "readaloud.log"
TMP_DIR = APP_DIR / "tmp"


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def notify_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "ReadAloudTTS", 0x10)
    except Exception:
        pass


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def set_voice(voice_id: str) -> None:
    config = load_config()
    voices = config.get("voices", {})
    if voice_id not in voices:
        raise SystemExit(f"Unknown voice: {voice_id}")
    config["current_voice"] = voice_id
    save_config(config)
    logging.info("Voice changed to %s", voice_id)


def list_voices() -> None:
    config = load_config()
    current = config.get("current_voice")
    for voice_id, voice in config.get("voices", {}).items():
        marker = "*" if voice_id == current else " "
        label = voice.get("label", voice_id)
        print(f"{marker} {voice_id}: {label}")


def find_piper_command() -> list[str]:
    candidates = [
        APP_DIR / ".venv" / "Scripts" / "piper.exe",
        APP_DIR / ".venv" / "Scripts" / "piper-tts.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return [str(candidate)]
    return [sys.executable, "-m", "piper"]


def normalize_text(text: str, max_chars: int) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def chunk_text(text: str, chunk_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []

    for paragraph in paragraphs:
        if len(paragraph) <= chunk_chars:
            chunks.append(paragraph)
            continue

        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        current = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(current) + len(sentence) + 1 <= chunk_chars:
                current = f"{current} {sentence}".strip()
                continue

            if current:
                chunks.append(current)
            if len(sentence) <= chunk_chars:
                current = sentence
            else:
                chunks.extend(textwrap.wrap(sentence, width=chunk_chars))
                current = ""
        if current:
            chunks.append(current)

    return chunks


def synthesize_chunk(
    piper_command: list[str],
    model: Path,
    voice_config: Path,
    text: str,
    wav_path: Path,
) -> None:
    command = [
        *piper_command,
        "--model",
        str(model),
        "--config",
        str(voice_config),
        "--output_file",
        str(wav_path),
    ]
    subprocess.run(
        command,
        input=text,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def speak_text(text: str) -> None:
    config = load_config()
    voice_id = config.get("current_voice")
    voices = config.get("voices", {})
    if voice_id not in voices:
        raise SystemExit(f"Configured voice is missing: {voice_id}")

    voice = voices[voice_id]
    model = APP_DIR / voice["model"]
    voice_config = APP_DIR / voice["config"]
    if not model.exists() or not voice_config.exists():
        raise SystemExit(f"Voice files are missing for {voice_id}. Run download_voices.ps1.")

    max_chars = int(config.get("max_chars", 30000))
    chunk_chars = int(config.get("chunk_chars", 900))
    text = normalize_text(text, max_chars)
    if not text:
        raise SystemExit("No text to speak.")

    chunks = chunk_text(text, chunk_chars)
    piper_command = find_piper_command()
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    logging.info("Speaking %s chars using %s in %s chunks", len(text), voice_id, len(chunks))
    with tempfile.TemporaryDirectory(prefix="readaloud-", dir=TMP_DIR) as temp_dir:
        temp_root = Path(temp_dir)
        for index, chunk in enumerate(chunks, start=1):
            wav_path = temp_root / f"chunk-{index:04d}.wav"
            synthesize_chunk(piper_command, model, voice_config, chunk, wav_path)
            winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)


def read_text_arg(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.input_file:
        input_path = Path(args.input_file)
        try:
            return input_path.read_text(encoding="utf-8")
        finally:
            if args.delete_input_file:
                input_path.unlink(missing_ok=True)
    return sys.stdin.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="ReadAloudTTS helper")
    parser.add_argument("--input-file")
    parser.add_argument("--text")
    parser.add_argument("--set-voice")
    parser.add_argument("--list-voices", action="store_true")
    parser.add_argument("--delete-input-file", action="store_true")
    args = parser.parse_args()

    setup_logging()
    try:
        if args.set_voice:
            set_voice(args.set_voice)
            return 0
        if args.list_voices:
            list_voices()
            return 0
        speak_text(read_text_arg(args))
        return 0
    except subprocess.CalledProcessError as error:
        logging.exception("Piper failed: %s", error.stderr)
        message = error.stderr or str(error)
        notify_error(f"Piper failed:\n\n{message[:1200]}")
        print(message, file=sys.stderr)
        return 1
    except Exception as error:
        logging.exception("Read aloud failed")
        notify_error(f"Read aloud failed:\n\n{str(error)[:1200]}")
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
