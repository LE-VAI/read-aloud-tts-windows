"""
TTS engine factory for ReadAloudTTS.
"""
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from .base import BaseTTSEngine
from .piper_engine import PiperTTSEngine
from .espeak_engine import ESpeakNGEngine


class TTSEngineFactory:
    """Factory for creating TTS engines."""
    
    # Supported engines
    PIPER = "piper"
    ESPEAK_NG = "espeak-ng"
    # FESTIVAL = "festival"  # Future implementation
    
    def __init__(self, config: Dict[str, Any], app_dir: Path):
        self.config = config
        self.app_dir = app_dir
        self.current_engine: Optional[BaseTTSEngine] = None
        self.current_engine_type: Optional[str] = None
    
    def create_engine(self, engine_type: str) -> Optional[BaseTTSEngine]:
        """Create a TTS engine of the specified type."""
        try:
            if engine_type == self.PIPER:
                engine = PiperTTSEngine(self.config, self.app_dir)
            elif engine_type == self.ESPEAK_NG:
                engine = ESpeakNGEngine(self.config, self.app_dir)
            #elif engine_type == self.FESTIVAL:
            #    engine = FestivalEngine(self.config, self.app_dir)
            else:
                logging.error(f"Unsupported engine type: {engine_type}")
                return None
            
            # Initialize the engine
            if engine.initialize():
                return engine
            else:
                logging.error(f"Failed to initialize {engine_type} engine")
                return None
        except Exception as e:
            logging.error(f"Failed to create {engine_type} engine: {e}")
            return None
    
    def set_engine(self, engine_type: str) -> bool:
        """Set the current TTS engine."""
        # If we're already using this engine, no need to change
        if self.current_engine_type == engine_type and self.current_engine is not None:
            return True
        
        # Create and initialize the new engine
        engine = self.create_engine(engine_type)
        if engine is not None:
            self.current_engine = engine
            self.current_engine_type = engine_type
            logging.info(f"TTS engine set to {engine_type}")
            return True
        else:
            logging.error(f"Failed to set TTS engine to {engine_type}")
            return False
    
    def get_current_engine(self) -> Optional[BaseTTSEngine]:
        """Get the current TTS engine."""
        return self.current_engine
    
    def get_available_engines(self) -> Dict[str, str]:
        """Get available TTS engines."""
        engines = {
            self.PIPER: "Piper (Neural TTS)",
            self.ESPEAK_NG: "eSpeak NG (Linguistic TTS)",
            # self.FESTIVAL: "Festival (Linguistic TTS)"  # Future implementation
        }
        return engines
    
    def speak_with_engine(self, engine_type: str, text: str, **kwargs) -> bool:
        """Speak text with a specific engine."""
        # Set the engine if it's not already the current one
        if self.current_engine_type != engine_type or self.current_engine is None:
            if not self.set_engine(engine_type):
                # Try fallback to Piper
                if engine_type != self.PIPER and self.set_engine(self.PIPER):
                    logging.warning(f"Falling back to Piper engine for text: {text[:50]}...")
                else:
                    return False
        
        # Speak with the current engine
        if self.current_engine is not None:
            return self.current_engine.speak(text, **kwargs)
        else:
            return False