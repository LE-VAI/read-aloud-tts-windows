"""
eSpeak NG TTS engine implementation.
"""
import logging
import subprocess
import sys
import winsound
from pathlib import Path
from typing import Dict, Any

from .base import BaseTTSEngine


class ESpeakNGEngine(BaseTTSEngine):
    """eSpeak NG TTS engine implementation."""
    
    def __init__(self, config: Dict[str, Any], app_dir: Path):
        super().__init__(config)
        self.app_dir = app_dir
        self.current_voice = "en"
        self.is_initialized = False
        
    def initialize(self) -> bool:
        """Initialize the eSpeak NG engine."""
        try:
            # Check if eSpeak NG is available
            result = subprocess.run(
                ["espeak-ng", "--version"],
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                self.is_initialized = True
                # Set default voice
                default_voice = self.config.get("current_voice", "en")
                self.set_voice(default_voice)
                logging.info("eSpeak NG engine initialized successfully")
                return True
            else:
                logging.error("eSpeak NG not found or not working properly")
                return False
        except FileNotFoundError:
            logging.error("eSpeak NG not found in PATH")
            return False
        except Exception as e:
            logging.error(f"Failed to initialize eSpeak NG engine: {e}")
            return False
    
    def speak(self, text: str, **kwargs) -> bool:
        """Speak the given text."""
        if not self.is_initialized:
            logging.error("eSpeak NG engine not initialized")
            return False
            
        try:
            # Create command
            command = ["espeak-ng"]
            
            # Apply voice settings
            command.extend(["-v", self.current_voice])
            
            # Apply prosody controls
            pitch = kwargs.get('pitch', 50)  # Default 50 (range 0-99)
            speed = kwargs.get('speed', 175)  # Default 175 words per minute
            volume = kwargs.get('volume', 100)  # Default 100 (range 0-200)
            
            command.extend(["-p", str(pitch)])
            command.extend(["-s", str(speed)])
            command.extend(["-a", str(volume)])
            
            # Add text
            command.extend([text])
            
            # Run eSpeak NG
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            
            if result.returncode == 0:
                logging.info("Text spoken successfully with eSpeak NG")
                return True
            else:
                logging.error(f"eSpeak NG failed: {result.stderr}")
                return False
        except Exception as e:
            logging.error(f"Failed to speak text with eSpeak NG: {e}")
            return False
    
    def stop(self) -> bool:
        """Stop current speech playback."""
        # eSpeak NG doesn't have a built-in stop mechanism when run as a subprocess
        # In a more sophisticated implementation, we might use a different approach
        return True
    
    def is_speaking(self) -> bool:
        """Check if the engine is currently speaking."""
        # Since we're running synchronously, we're never speaking in the background
        return False
    
    def get_voices(self) -> Dict[str, str]:
        """Get available voices for this engine."""
        # Return a predefined list of common eSpeak NG voices
        return {
            "en": "English",
            "en-us": "English (US)",
            "en-gb": "English (UK)",
            "en-uk-north": "English (Northern)",
            "en-uk-rp": "English (Received Pronunciation)",
            "en-uk-wmids": "English (Midlands)",
            "en-us-nyc": "English (New York City)",
            "fr": "French",
            "de": "German",
            "es": "Spanish",
            "it": "Italian",
        }
    
    def set_voice(self, voice_id: str) -> bool:
        """Set the current voice."""
        voices = self.get_voices()
        if voice_id in voices:
            self.current_voice = voice_id
            logging.info(f"Voice set to {voice_id}")
            return True
        else:
            logging.warning(f"Voice {voice_id} not found, using default")
            return False