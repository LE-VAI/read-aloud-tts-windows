import argparse
import ctypes
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unicodedata
import winsound
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "logs" / "readaloud.log"
TMP_DIR = APP_DIR / "tmp"
PIPER_TIMEOUT_SECONDS = 90
UNICODE_REPLACEMENTS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": " — ",
        "\u2026": "...",
        "\u00a0": " ",
    }
)


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
    # Always invoke Piper through the same Python interpreter that is running
    # this script (sys.executable -m piper). The standalone piper.exe shipped
    # in the venv is a zip-app launcher that resolves its own interpreter from
    # PATH and ends up running miniconda3\python.exe (which lacks Piper's deps),
    # adding a silent failed-process penalty or a wrong-interpreter race.
    # Pinning sys.executable keeps everything inside the venv that has Piper.
    return [sys.executable, "-m", "piper"]


def cleanup_stale_temp_audio(current_run_dir: Path | None = None) -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    current_run_dir = current_run_dir.resolve() if current_run_dir else None

    for temp_dir in TMP_DIR.glob("readaloud-*"):
        try:
            if current_run_dir and temp_dir.resolve() == current_run_dir:
                continue
            if temp_dir.is_dir():
                shutil.rmtree(temp_dir, ignore_errors=True)
        except OSError:
            logging.warning("Could not remove stale temp folder: %s", temp_dir)

    for loose_wav in TMP_DIR.glob("*.wav"):
        try:
            loose_wav.unlink(missing_ok=True)
        except OSError:
            logging.warning("Could not remove stale temp audio: %s", loose_wav)


def normalize_text(text: str, max_chars: int) -> str:
    text = sanitize_text(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return text


def sanitize_text(text: str) -> str:
    text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    text = text.translate(UNICODE_REPLACEMENTS)
    text = text.replace("\ufeff", "")

    # Verbalize common symbols instead of silently deleting them (the S*
    # category fallback below turns unknown symbols into a bare space, which
    # sounds like a skipped word). These replacements give Piper phoneme
    # cues to pronounce the symbol's meaning.
    symbol_words = {
        "$": " dollars ",
        "%": " percent ",
        "&": " and ",
        "+": " plus ",
        "=": " equals ",
        "@": " at ",
        "#": " hashtag ",
        "*": " ",
    }
    for symbol, word in symbol_words.items():
        text = text.replace(symbol, word)

    cleaned: list[str] = []
    for char in text:
        codepoint = ord(char)
        if 0xD800 <= codepoint <= 0xDFFF:
            cleaned.append("\ufffd")
            continue

        category = unicodedata.category(char)
        if char in ("\n", "\t"):
            cleaned.append(char)
        elif category == "Cc":
            cleaned.append(" ")
        elif category == "Cf":
            continue
        elif category.startswith("S"):
            cleaned.append(" ")
        else:
            cleaned.append(char)

    return "".join(cleaned)


def chunk_text(text: str, chunk_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []

    # Accumulate paragraphs into the current chunk up to chunk_chars before
    # emitting. The old code emitted one chunk per paragraph (even short ones),
    # so a 17-paragraph selection became 17 chunks = 17 Piper cold starts.
    # Merging short paragraphs cuts chunk count 3-5x for typical selections.
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_chars:
            current = f"{current}\n\n{paragraph}" if current else paragraph
            continue

        # Current chunk is full — emit it and start a new one.
        if current:
            chunks.append(current)
            current = ""

        # If this paragraph alone fits, start the new chunk with it.
        if len(paragraph) <= chunk_chars:
            current = paragraph
            continue

        # Paragraph exceeds chunk_chars — split it by sentences, accumulating
        # sentences into the current chunk up to chunk_chars.
        sentences = re.split(r"(?<=[.!?])\s+", paragraph)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(current) + len(sentence) + 1 <= chunk_chars:
                current = f"{current} {sentence}".strip() if current else sentence
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
    prosody: dict[str, Any] | None = None,
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
    # Optional prosody controls from config.json. Defaults match the voice
    # model's baked-in inference block so these are non-breaking.
    if prosody:
        if "sentence_silence" in prosody:
            command.extend(["--sentence-silence", str(prosody["sentence_silence"])])
        if "length_scale" in prosody:
            command.extend(["--length-scale", str(prosody["length_scale"])])
        if "noise_scale" in prosody:
            command.extend(["--noise-scale", str(prosody["noise_scale"])])
        if "noise_w" in prosody:
            command.extend(["--noise-w", str(prosody["noise_w"])])
    subprocess.run(
        command,
        input=text,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=PIPER_TIMEOUT_SECONDS,
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
    cleanup_stale_temp_audio()

    # Prosody controls from config.json. Defaults match the voice model's
    # baked-in inference block, so these are non-breaking. The operator can
    # tune them in config.json without code changes.
    prosody: dict[str, Any] = {}
    if "sentence_silence" in config:
        prosody["sentence_silence"] = float(config["sentence_silence"])
    if "length_scale" in config:
        prosody["length_scale"] = float(config["length_scale"])
    if "noise_scale" in config:
        prosody["noise_scale"] = float(config["noise_scale"])
    if "noise_w" in config:
        prosody["noise_w"] = float(config["noise_w"])
    # Inter-chunk pause seconds — a small silence between WAV playbacks so
    # chunk boundaries don't sound like an abrupt mid-sentence merger.
    inter_chunk_pause = float(config.get("inter_chunk_pause", 0.3))

    logging.info("Speaking %s chars using %s in %s chunks", len(text), voice_id, len(chunks))
    temp_root = Path(tempfile.mkdtemp(prefix="readaloud-", dir=TMP_DIR)).resolve()
    generated_wavs: list[Path] = []
    try:
        cleanup_stale_temp_audio(current_run_dir=temp_root)

        if len(chunks) == 1:
            # Single chunk — no pipelining needed, simple path.
            wav_path = temp_root / "chunk-0001.wav"
            synthesize_chunk(piper_command, model, voice_config, chunks[0], wav_path, prosody)
            generated_wavs.append(wav_path)
            winsound.PlaySound(str(wav_path), winsound.SND_FILENAME)
        else:
            # Pipeline: pre-synthesize chunk N+1 in a worker thread while
            # chunk N plays on the main thread. This hides all but the first
            # Piper cold start behind audio playback time.
            def synth_worker(index: int, chunk: str, out_path: Path) -> None:
                try:
                    synthesize_chunk(piper_command, model, voice_config, chunk, out_path, prosody)
                except Exception as e:
                    logging.error("Worker failed on chunk %s: %s", index, e)

            # Synthesize chunk 1 synchronously (first audio must wait for it).
            first_wav = temp_root / "chunk-0001.wav"
            synthesize_chunk(piper_command, model, voice_config, chunks[0], first_wav, prosody)
            generated_wavs.append(first_wav)

            for index in range(1, len(chunks)):
                # Start synthesizing the NEXT chunk in the background.
                next_wav = temp_root / f"chunk-{index + 1:04d}.wav"
                worker = threading.Thread(
                    target=synth_worker,
                    args=(index + 1, chunks[index], next_wav),
                    daemon=True,
                )
                worker.start()
                # Play the CURRENT chunk while the worker synthesizes the next.
                winsound.PlaySound(str(first_wav), winsound.SND_FILENAME)
                # Small inter-chunk pause so the boundary doesn't sound abrupt.
                if inter_chunk_pause > 0:
                    time.sleep(inter_chunk_pause)
                generated_wavs.append(next_wav)
                # Wait for the worker to finish before swapping.
                worker.join()
                first_wav = next_wav
            # Play the final chunk.
            winsound.PlaySound(str(first_wav), winsound.SND_FILENAME)
    finally:
        for wav_path in generated_wavs:
            try:
                wav_path.unlink(missing_ok=True)
            except OSError:
                logging.warning("Could not remove temp audio: %s", wav_path)
        shutil.rmtree(temp_root, ignore_errors=True)


def read_text_arg(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.input_file:
        input_path = Path(args.input_file)
        try:
            return input_path.read_text(encoding="utf-8-sig", errors="replace")
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
