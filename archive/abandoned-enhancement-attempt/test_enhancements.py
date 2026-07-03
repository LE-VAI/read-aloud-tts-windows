"""
Test script for ReadAloudTTS enhancements.
"""
import sys
import os
import json
import time
from pathlib import Path

# Add the tts package to the path
sys.path.append(str(Path(__file__).resolve().parent))

from tts.factory import TTSEngineFactory
from tts.ssml import SSMLProcessor
from tts.pronunciation import PronunciationDictionary


def test_multi_engine_support():
    """Test multi-engine TTS support."""
    print("Testing multi-engine TTS support...")
    
    # Create a simple config for testing
    config = {
        "current_voice": "en_US-lessac-medium",
        "default_engine": "piper",
        "voices": {
            "en_US-lessac-medium": {
                "label": "Lessac - warm",
                "model": "voices/en_US-lessac-medium.onnx",
                "config": "voices/en_US-lessac-medium.onnx.json"
            }
        }
    }
    
    app_dir = Path(__file__).resolve().parent
    factory = TTSEngineFactory(config, app_dir)
    
    # Test available engines
    engines = factory.get_available_engines()
    print(f"Available engines: {engines}")
    
    # Test setting Piper engine
    if factory.set_engine(TTSEngineFactory.PIPER):
        print("✓ Successfully set Piper engine")
    else:
        print("✗ Failed to set Piper engine")
    
    # Test setting eSpeak NG engine (if available)
    if factory.set_engine(TTSEngineFactory.ESPEAK_NG):
        print("✓ Successfully set eSpeak NG engine")
    else:
        print("⚠ eSpeak NG not available or not installed")
    
    print()


def test_ssml_support():
    """Test SSML support."""
    print("Testing SSML support...")
    
    processor = SSMLProcessor()
    
    # Test SSML generation
    ssml = processor.generate_ssml(
        "This is a test of SSML generation.",
        pitch=65,
        speed=1.3,
        volume=85
    )
    print(f"Generated SSML: {ssml}")
    
    # Test SSML parsing
    test_ssml = '''<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
        <prosody pitch="60" rate="1.2" volume="80">
            This is a test of SSML processing.
        </prosody>
    </speak>'''
    
    params = processor.parse_ssml(test_ssml)
    print(f"Parsed parameters: {params}")
    
    # Test SSML validation
    is_valid = processor.validate_ssml(ssml)
    print(f"SSML validation: {'✓ Valid' if is_valid else '✗ Invalid'}")
    
    print()


def test_pronunciation_dictionary():
    """Test pronunciation dictionary."""
    print("Testing pronunciation dictionary...")
    
    # Create a temporary dictionary file
    dict_path = Path("test_dict.json")
    
    # Create dictionary
    dictionary = PronunciationDictionary(dict_path)
    
    # Test adding entries
    dictionary.add_entry("TTS", "T-T-S", "acronym")
    dictionary.add_entry("AI", "A-I", "acronym")
    dictionary.add_entry("ML", "M-L", "acronym")
    
    # Test getting pronunciation
    tts_pron = dictionary.get_pronunciation("TTS")
    print(f"Pronunciation of TTS: {tts_pron}")
    
    # Test applying pronunciation to text
    test_text = "The TTS system uses AI and ML technologies."
    processed_text = dictionary.apply_pronunciation(test_text)
    print(f"Original text: {test_text}")
    print(f"Processed text: {processed_text}")
    
    # Test listing entries
    entries = dictionary.dictionary
    print(f"Dictionary entries: {len(entries)}")
    
    # Clean up
    if dict_path.exists():
        dict_path.unlink()
    
    print()


def main():
    """Main test function."""
    print("ReadAloudTTS Enhancement Tests")
    print("=" * 40)
    
    # Run tests
    test_multi_engine_support()
    test_ssml_support()
    test_pronunciation_dictionary()
    
    print("All tests completed!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())