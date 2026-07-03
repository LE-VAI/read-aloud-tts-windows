"""
Piper TTS engine implementation.
"""
import logging
import subprocess
import sys
import winsound
from pathlib import Path
from typing import Dict, Any, Optional

from .base import BaseTTSEngine


class PiperTTSEngine(BaseTTSEngine):
    """Piper TTS engine implementation."""
    
    def __init__(self, config: Dict[str, Any], app_dir: Path):
        super().__init__(config)
        self.app_dir = app_dir
        self.piper_process = None
        self.current_voice_id = None
        self.current_model = None
        self.current_voice_config = None
        self.piper_command = None
        
    def find_piper_command(self) -> list[str]:
        """Find the Piper executable.

        Always invoke Piper through the same Python interpreter that is
        running the service (``sys.executable -m piper``).  The standalone
        ``piper.exe`` shipped in the venv is a zip-app launcher that resolves
        its own interpreter from PATH and ends up running
        ``miniconda3\\python.exe`` (which lacks Piper's deps), producing a
        silent second process that races the real one on the same model
        files.  Pinning ``sys.executable`` keeps everything inside the venv
        that actually has Piper installed.
        """
        return [sys.executable, "-m", "piper"]
    
    def initialize(self) -> bool:
        """Initialize the Piper TTS engine."""
        try:
            self.piper_command = self.find_piper_command()
            # Preload the default voice
            default_voice = self.config.get("current_voice", "en_US-lessac-medium")
            return self.set_voice(default_voice)
        except Exception as e:
            logging.error(f"Failed to initialize Piper TTS engine: {e}")
            return False
    
    def set_voice(self, voice_id: str) -> bool:
        """Set the current voice."""
        try:
            voices = self.config.get("voices", {})
            if voice_id not in voices:
                logging.error(f"Voice not found: {voice_id}")
                return False

            voice = voices[voice_id]
            model = self.app_dir / voice["model"]
            voice_config = self.app_dir / voice["config"]
            
            if not model.exists() or not voice_config.exists():
                logging.error(f"Voice files are missing for {voice_id}")
                return False

            # If we're already using this voice, no need to reload
            if (self.current_voice_id == voice_id and 
                self.current_model == model and 
                self.current_voice_config == voice_config and
                self.piper_process is not None and
                self.piper_process.poll() is None):
                logging.info(f"Model already loaded for voice: {voice_id}")
                return True

            # Clean up existing process if any
            if self.piper_process is not None:
                try:
                    self.piper_process.terminate()
                    self.piper_process.wait(timeout=5)
                except Exception:
                    pass

            # Start Piper in server mode (waiting for input)
            command = [
                *self.piper_command,
                "--model",
                str(model),
                "--config",
                str(voice_config),
                "--output_raw"  # Output raw audio data
            ]
            
            logging.info(f"Preloading Piper model for voice: {voice_id}")
            self.piper_process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            
            # Update globals
            self.current_voice_id = voice_id
            self.current_model = model
            self.current_voice_config = voice_config
            
            logging.info(f"Successfully preloaded Piper model for voice: {voice_id}")
            return True
        except Exception as e:
            logging.error(f"Failed to set voice: {e}")
            self.piper_process = None
            return False
    
    def speak(self, text: str, **kwargs) -> bool:
        """Speak the given text."""
        if self.piper_process is None or self.piper_process.poll() is not None:
            logging.error("Piper process not available or has terminated")
            # Try to reload the model
            if not self.set_voice(self.current_voice_id or self.config.get("current_voice", "en_US-lessac-medium")):
                return False

        try:
            # Get text processing parameters
            max_chars = int(self.config.get("max_chars", 30000))
            chunk_chars = int(self.config.get("chunk_chars", 900))
            
            # Process text
            text = self._normalize_text(text, max_chars)
            if not text:
                logging.warning("No text to speak")
                return True

            chunks = self._chunk_text(text, chunk_chars)
            
            logging.info(f"Speaking {len(text)} chars in {len(chunks)} chunks")
            
            for chunk in chunks:
                # For each chunk, we need to run Piper as a separate process
                # because the streaming approach is complex. Use a temp WAV
                # path via -f so Piper writes a valid WAV file directly (without
                # -f, Piper tries ffplay playback and falls back to output.wav
                # in CWD; without --output_raw it does NOT write WAV to stdout,
                # so capturing stdout yields empty/garbage and PlaySound fails).
                import tempfile, os
                tmp_dir = self.app_dir / "tmp"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                temp_wav = tempfile.NamedTemporaryFile(
                    suffix=".wav", delete=False, dir=str(tmp_dir)
                )
                temp_path = temp_wav.name
                temp_wav.close()  # let Piper write to it

                command = [
                    *self.piper_command,
                    "--model",
                    str(self.current_model),
                    "--config",
                    str(self.current_voice_config),
                    "-f",
                    temp_path,
                ]

                # Apply prosody controls if provided
                pitch = kwargs.get('pitch')
                speed = kwargs.get('speed')
                volume = kwargs.get('volume')

                if pitch is not None:
                    command.extend(["--sentence-silence", str(pitch)])
                if speed is not None:
                    command.extend(["--length-scale", str(1.0 / speed)])

                result = subprocess.run(
                    command,
                    input=chunk.encode("utf-8"),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

                winsound.PlaySound(temp_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)

                # Clean up temp file
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                
            return True
        except Exception as e:
            logging.error(f"Failed to speak text with Piper: {e}")
            return False
    
    def stop(self) -> bool:
        """Stop current speech playback."""
        # Since we're playing synchronously, we can't easily stop
        # A more sophisticated implementation would use async playback
        return True
    
    def is_speaking(self) -> bool:
        """Check if the engine is currently speaking."""
        # Since we're playing synchronously, we're never "speaking" in the background
        return False
    
    def get_voices(self) -> Dict[str, str]:
        """Get available voices for this engine."""
        voices = self.config.get("voices", {})
        return {vid: v.get("label", vid) for vid, v in voices.items()}
    
    def _normalize_text(self, text: str, max_chars: int) -> str:
        """Normalize text for TTS."""
        import re
        import unicodedata
        
        # Unicode replacements
        UNICODE_REPLACEMENTS = str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201c": '"',
                "\u201d": '"',
                "\u2013": "-",
                "\u2014": "-",
                "\u2026": "...",
                "\u00a0": " ",
            }
        )
        
        text = text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
        text = text.translate(UNICODE_REPLACEMENTS)
        text = text.replace("\ufeff", "")

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

        text = "".join(cleaned)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        return text
    
    def _chunk_text(self, text: str, chunk_chars: int) -> list[str]:
        """Chunk text for TTS."""
        import re
        import textwrap
        
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