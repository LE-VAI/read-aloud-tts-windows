#!/usr/bin/env python3
"""
Enhanced persistent TTS service for ReadAloudTTS.
This service supports multiple TTS engines and stays running in the background,
eliminating process creation overhead for each request.
"""

import argparse
import ctypes
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unicodedata
import winsound
import win32pipe
import win32file
import pywintypes
from pathlib import Path
from typing import Any, Optional

# Add the tts package to the path
sys.path.append(str(Path(__file__).resolve().parent))

from tts.factory import TTSEngineFactory
from tts.ssml import SSMLProcessor
from tts.pronunciation import PronunciationDictionary


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "logs" / "readaloud_service.log"
TMP_DIR = APP_DIR / "tmp"
PIPE_NAME = r"\\.\pipe\ReadAloudTTS"
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


class TTSService:
    def __init__(self):
        self.tts_factory = TTSEngineFactory(self.load_config(), APP_DIR)
        self.ssml_processor = SSMLProcessor()
        self.pronunciation_dict = PronunciationDictionary(APP_DIR / "pronunciation_dict.json")
        self.current_engine = None
        self.service_running = True
        self.pipe_handle = None
        self.setup_logging()

    def setup_logging(self) -> None:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Force configuration even if some imported module already touched the
        # root logger (basicConfig is a no-op once a handler exists). Attach an
        # explicit FileHandler so service output always lands in the log file.
        root = logging.getLogger()
        # Remove any pre-existing handlers that would swallow our config.
        for handler in list(root.handlers):
            root.removeHandler(handler)
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    def notify_error(self, message: str) -> None:
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "ReadAloudTTS Service", 0x10)
        except Exception:
            pass

    def load_config(self) -> dict[str, Any]:
        with CONFIG_PATH.open("r", encoding="utf-8") as file:
            return json.load(file)

    def find_piper_command(self) -> list[str]:
        candidates = [
            APP_DIR / ".venv" / "Scripts" / "piper.exe",
            APP_DIR / ".venv" / "Scripts" / "piper-tts.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return [str(candidate)]
        return [sys.executable, "-m", "piper"]

    def cleanup_stale_temp_audio(self, current_run_dir: Path | None = None) -> None:
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

    def normalize_text(self, text: str, max_chars: int) -> str:
        text = self.sanitize_text(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()
        if len(text) > max_chars:
            text = text[:max_chars].rstrip() + "..."
        return text

    def sanitize_text(self, text: str) -> str:
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

        return "".join(cleaned)

    def chunk_text(self, text: str, chunk_chars: int) -> list[str]:
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

    def preload_piper_model(self, voice_id: str) -> bool:
        """
        Preload the Piper TTS model for the specified voice.
        Returns True if successful, False otherwise.
        """
        # Use the TTS factory instead
        return self.tts_factory.set_engine(TTSEngineFactory.PIPER)

    def speak_text_with_preloaded_model(self, text: str, **kwargs) -> bool:
        """
        Speak text using the current TTS engine.
        Returns True if successful, False otherwise.
        """
        try:
            # Check if text is SSML
            is_ssml = kwargs.get('ssml', False)
            params = {'text': text}
            
            if is_ssml:
                # Parse SSML to extract parameters
                params = self.ssml_processor.parse_ssml(text)
                # Override kwargs with SSML parameters
                kwargs.update({k: v for k, v in params.items() if k != 'text'})
                # Use the extracted text
                text = params.get('text', text)
            
            # Apply pronunciation dictionary
            use_dict = kwargs.get('use_pronunciation_dict', True)
            if use_dict:
                text = self.pronunciation_dict.apply_pronunciation(text)
            
            # Load config for text processing parameters
            config = self.load_config()
            max_chars = int(config.get("max_chars", 30000))
            chunk_chars = int(config.get("chunk_chars", 900))
            
            # Process text
            text = self.normalize_text(text, max_chars)
            if not text:
                logging.warning("No text to speak")
                return True

            # Use the current engine from the factory
            engine = self.tts_factory.get_current_engine()
            if engine is None:
                # Try to initialize the default engine
                default_engine = config.get("default_engine", TTSEngineFactory.PIPER)
                if not self.tts_factory.set_engine(default_engine):
                    logging.error("Failed to initialize any TTS engine")
                    return False
                engine = self.tts_factory.get_current_engine()
            
            if engine is not None:
                # For now, we'll speak the entire text at once
                # A more sophisticated implementation would handle chunking in the engine
                return engine.speak(text, **kwargs)
            else:
                logging.error("No TTS engine available")
                return False
        except Exception as e:
            logging.error(f"Failed to speak text: {e}")
            return False

    def handle_request(self, request_str: str) -> str:
        """Handle a request from the client."""
        try:
            request = json.loads(request_str)
            action = request.get('action')
            
            if action == 'speak':
                text = request.get('text', '')
                # Extract prosody parameters
                pitch = request.get('pitch')
                speed = request.get('speed')
                volume = request.get('volume')
                engine = request.get('engine')
                ssml = request.get('ssml', False)
                voice = request.get('voice')
                use_dict = request.get('use_pronunciation_dict', True)
                
                # Prepare kwargs for the speak method
                kwargs = {}
                if pitch is not None:
                    kwargs['pitch'] = pitch
                if speed is not None:
                    kwargs['speed'] = speed
                if volume is not None:
                    kwargs['volume'] = volume
                if ssml is not None:
                    kwargs['ssml'] = ssml
                if voice is not None:
                    kwargs['voice'] = voice
                if use_dict is not None:
                    kwargs['use_pronunciation_dict'] = use_dict
                
                # If engine is specified, use it
                if engine is not None:
                    if self.tts_factory.speak_with_engine(engine, text, **kwargs):
                        return json.dumps({'status': 'success', 'message': 'Text spoken successfully'})
                    else:
                        return json.dumps({'status': 'error', 'message': 'Failed to speak text'})
                else:
                    # Use current engine
                    if self.speak_text_with_preloaded_model(text, **kwargs):
                        return json.dumps({'status': 'success', 'message': 'Text spoken successfully'})
                    else:
                        return json.dumps({'status': 'error', 'message': 'Failed to speak text'})
                        
            elif action == 'set_voice':
                voice_id = request.get('voice_id')
                # For Piper engine, we need to set the voice in the config
                # For other engines, we can set it directly
                engine = self.tts_factory.get_current_engine()
                if engine is not None:
                    if engine.set_voice(voice_id):
                        return json.dumps({'status': 'success', 'message': f'Voice set to {voice_id}'})
                    else:
                        return json.dumps({'status': 'error', 'message': f'Failed to set voice to {voice_id}'})
                else:
                    # Try to preload Piper model as fallback
                    if self.preload_piper_model(voice_id):
                        return json.dumps({'status': 'success', 'message': f'Voice set to {voice_id}'})
                    else:
                        return json.dumps({'status': 'error', 'message': f'Failed to set voice to {voice_id}'})
                        
            elif action == 'get_voice':
                engine = self.tts_factory.get_current_engine()
                if engine is not None:
                    # For now, we'll return a placeholder
                    # A more sophisticated implementation would get the actual current voice
                    return json.dumps({'status': 'success', 'voice_id': 'unknown'})
                else:
                    return json.dumps({'status': 'success', 'voice_id': 'unknown'})
                    
            elif action == 'set_engine':
                engine_type = request.get('engine_type')
                if self.tts_factory.set_engine(engine_type):
                    return json.dumps({'status': 'success', 'message': f'Engine set to {engine_type}'})
                else:
                    return json.dumps({'status': 'error', 'message': f'Failed to set engine to {engine_type}'})
                    
            elif action == 'get_engines':
                engines = self.tts_factory.get_available_engines()
                return json.dumps({'status': 'success', 'engines': engines})
                
            elif action == 'parse_ssml':
                ssml_text = request.get('ssml_text', '')
                params = self.ssml_processor.parse_ssml(ssml_text)
                return json.dumps({'status': 'success', 'params': params})
                
            elif action == 'generate_ssml':
                text = request.get('text', '')
                pitch = request.get('pitch')
                speed = request.get('speed')
                volume = request.get('volume')
                voice = request.get('voice')
                
                kwargs = {}
                if pitch is not None:
                    kwargs['pitch'] = pitch
                if speed is not None:
                    kwargs['speed'] = speed
                if volume is not None:
                    kwargs['volume'] = volume
                if voice is not None:
                    kwargs['voice'] = voice
                
                ssml = self.ssml_processor.generate_ssml(text, **kwargs)
                return json.dumps({'status': 'success', 'ssml': ssml})
                
            elif action == 'add_pronunciation':
                word = request.get('word')
                pronunciation = request.get('pronunciation')
                word_type = request.get('type', 'default')
                language = request.get('language', 'en')
                priority = request.get('priority', 0)
                
                if self.pronunciation_dict.add_entry(word, pronunciation, word_type, language, priority):
                    self.pronunciation_dict.save_dictionary()
                    return json.dumps({'status': 'success', 'message': f'Added pronunciation for {word}'})
                else:
                    return json.dumps({'status': 'error', 'message': f'Failed to add pronunciation for {word}'})
                    
            elif action == 'remove_pronunciation':
                word = request.get('word')
                if self.pronunciation_dict.remove_entry(word):
                    self.pronunciation_dict.save_dictionary()
                    return json.dumps({'status': 'success', 'message': f'Removed pronunciation for {word}'})
                else:
                    return json.dumps({'status': 'error', 'message': f'Failed to remove pronunciation for {word}'})
                    
            elif action == 'get_pronunciation':
                word = request.get('word')
                language = request.get('language', 'en')
                pronunciation = self.pronunciation_dict.get_pronunciation(word, language)
                if pronunciation:
                    return json.dumps({'status': 'success', 'pronunciation': pronunciation})
                else:
                    return json.dumps({'status': 'error', 'message': f'Pronunciation not found for {word}'})
                    
            elif action == 'list_pronunciations':
                entries = self.pronunciation_dict.dictionary
                return json.dumps({'status': 'success', 'entries': entries})
                
            elif action == 'stop':
                # This would be for stopping speech
                engine = self.tts_factory.get_current_engine()
                if engine is not None:
                    if engine.stop():
                        return json.dumps({'status': 'success', 'message': 'Speech stopped'})
                    else:
                        return json.dumps({'status': 'error', 'message': 'Failed to stop speech'})
                else:
                    return json.dumps({'status': 'success', 'message': 'Stop command received'})
                
            else:
                return json.dumps({'status': 'error', 'message': 'Unknown action'})
                
        except Exception as e:
            logging.error(f"Error handling request: {e}")
            return json.dumps({'status': 'error', 'message': str(e)})

    def run_service(self) -> None:
        """Run the persistent TTS service."""
        logging.info("Starting ReadAloudTTS service")
        
        # Initialize the default engine
        config = self.load_config()
        default_engine = config.get("default_engine", TTSEngineFactory.PIPER)
        self.tts_factory.set_engine(default_engine)
        
        while self.service_running:
            try:
                # Create named pipe
                self.pipe_handle = win32pipe.CreateNamedPipe(
                    PIPE_NAME,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_WAIT,
                    1, 65536, 65536, 0, None
                )
                
                logging.info("Waiting for client connection...")
                win32pipe.ConnectNamedPipe(self.pipe_handle, None)
                logging.info("Client connected")
                
                # Read request
                result, request_data = win32file.ReadFile(self.pipe_handle, 65536)
                request_str = request_data.decode('utf-8')
                
                # Handle request
                response_str = self.handle_request(request_str)
                
                # Send response
                win32file.WriteFile(self.pipe_handle, response_str.encode('utf-8'))
                
                # Close pipe
                win32file.CloseHandle(self.pipe_handle)
                self.pipe_handle = None
                
            except pywintypes.error as e:
                if e.args[0] == 2:  # File not found (pipe was closed)
                    logging.info("Pipe closed, continuing...")
                    continue
                elif e.args[0] == 109:  # Pipe was closed by client
                    logging.info("Client disconnected")
                    if self.pipe_handle:
                        win32file.CloseHandle(self.pipe_handle)
                        self.pipe_handle = None
                    continue
                elif e.args[0] == 231:  # All pipe instances are busy
                    # A client connected to the previous instance but never
                    # closed it cleanly, leaving the single instance busy.
                    # Force-close any handle we hold and recreate the pipe.
                    logging.warning("Pipe instance stuck busy; recreating")
                    if self.pipe_handle:
                        try:
                            win32file.CloseHandle(self.pipe_handle)
                        except Exception:
                            pass
                        self.pipe_handle = None
                    time.sleep(0.5)
                    continue
                else:
                    logging.error(f"Pipe error: {e}")
                    if self.pipe_handle:
                        win32file.CloseHandle(self.pipe_handle)
                        self.pipe_handle = None
                    time.sleep(1)
            except Exception as e:
                logging.error(f"Service error: {e}")
                if self.pipe_handle:
                    win32file.CloseHandle(self.pipe_handle)
                    self.pipe_handle = None
                time.sleep(1)
                
        logging.info("ReadAloudTTS service stopped")

    def stop_service(self) -> None:
        """Stop the service."""
        self.service_running = False
        if self.pipe_handle:
            try:
                win32file.CloseHandle(self.pipe_handle)
            except Exception:
                pass
        # Delegate engine cleanup to the factory. The Piper subprocess lives
        # inside the engine, not on the service object, so ask the current
        # engine to stop rather than touching a non-existent self.piper_process.
        if self.tts_factory is not None:
            engine = self.tts_factory.get_current_engine()
            if engine is not None:
                try:
                    engine.stop()
                except Exception:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="ReadAloudTTS persistent service")
    parser.add_argument("--install-deps", action="store_true", help="Install required dependencies")
    args = parser.parse_args()
    
    if args.install_deps:
        # Install required dependencies
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pywin32"])
            print("Dependencies installed successfully")
            return 0
        except Exception as e:
            print(f"Failed to install dependencies: {e}")
            return 1
    
    service = TTSService()
    
    def signal_handler(signum, frame):
        """Handle shutdown signals."""
        logging.info("Received shutdown signal")
        service.stop_service()
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Run the service
        service.run_service()
        return 0
    except Exception as e:
        service.setup_logging()  # Make sure logging is set up
        logging.exception("Service failed")
        service.notify_error(f"ReadAloudTTS service failed:\n\n{str(e)[:1200]}")
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())