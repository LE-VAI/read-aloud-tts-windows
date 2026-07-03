"""
Base TTS engine interface for ReadAloudTTS.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class BaseTTSEngine(ABC):
    """Base class for all TTS engines."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
    
    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the TTS engine. Return True if successful."""
        pass
    
    @abstractmethod
    def speak(self, text: str, **kwargs) -> bool:
        """Speak the given text with optional parameters."""
        pass
    
    @abstractmethod
    def stop(self) -> bool:
        """Stop current speech playback."""
        pass
    
    @abstractmethod
    def is_speaking(self) -> bool:
        """Check if the engine is currently speaking."""
        pass
    
    @abstractmethod
    def get_voices(self) -> Dict[str, str]:
        """Get available voices for this engine."""
        pass
    
    @abstractmethod
    def set_voice(self, voice_id: str) -> bool:
        """Set the current voice."""
        pass