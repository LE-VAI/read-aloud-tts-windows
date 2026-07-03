"""
Pronunciation dictionary system for ReadAloudTTS.
"""
import json
import logging
import re
from pathlib import Path
from typing import Dict, Any, Optional, List


class PronunciationDictionary:
    """Manage pronunciation dictionaries for TTS engines."""
    
    def __init__(self, dict_path: Path = None):
        self.dict_path = dict_path or Path("pronunciation_dict.json")
        self.dictionary: Dict[str, Dict[str, Any]] = {}
        self.load_dictionary()
    
    def load_dictionary(self) -> bool:
        """
        Load pronunciation dictionary from file.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            if self.dict_path.exists():
                with open(self.dict_path, 'r', encoding='utf-8') as f:
                    self.dictionary = json.load(f)
                logging.info(f"Loaded pronunciation dictionary with {len(self.dictionary)} entries")
                return True
            else:
                # Create default dictionary
                self.dictionary = self._create_default_dictionary()
                self.save_dictionary()
                return True
        except Exception as e:
            logging.error(f"Failed to load pronunciation dictionary: {e}")
            self.dictionary = {}
            return False
    
    def save_dictionary(self) -> bool:
        """
        Save pronunciation dictionary to file.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create directory if it doesn't exist
            self.dict_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.dict_path, 'w', encoding='utf-8') as f:
                json.dump(self.dictionary, f, indent=2, ensure_ascii=False)
            logging.info(f"Saved pronunciation dictionary with {len(self.dictionary)} entries")
            return True
        except Exception as e:
            logging.error(f"Failed to save pronunciation dictionary: {e}")
            return False
    
    def _create_default_dictionary(self) -> Dict[str, Dict[str, Any]]:
        """Create a default pronunciation dictionary."""
        return {
            "CPU": {"pronunciation": "C-P-U", "type": "acronym"},
            "GPU": {"pronunciation": "G-P-U", "type": "acronym"},
            "RAM": {"pronunciation": "R-A-M", "type": "acronym"},
            "API": {"pronunciation": "A-P-I", "type": "acronym"},
            "URL": {"pronunciation": "U-R-L", "type": "acronym"},
            "HTML": {"pronunciation": "H-T-M-L", "type": "acronym"},
            "CSS": {"pronunciation": "C-S-S", "type": "acronym"},
            "JSON": {"pronunciation": "J-S-O-N", "type": "acronym"},
            "XML": {"pronunciation": "X-M-L", "type": "acronym"},
            "HTTP": {"pronunciation": "H-T-T-P", "type": "acronym"},
            "HTTPS": {"pronunciation": "H-T-T-P-S", "type": "acronym"},
            "UI": {"pronunciation": "U-I", "type": "acronym"},
            "UX": {"pronunciation": "U-X", "type": "acronym"},
            "CEO": {"pronunciation": "C-E-O", "type": "acronym"},
            "FAQ": {"pronunciation": "F-A-Q", "type": "acronym"},
            "FYI": {"pronunciation": "F-Y-I", "type": "acronym"},
            "ETA": {"pronunciation": "E-T-A", "type": "acronym"},
            "ASAP": {"pronunciation": "A-S-A-P", "type": "acronym"},
            "DIY": {"pronunciation": "D-I-Y", "type": "acronym"},
            "RSVP": {"pronunciation": "R-S-V-P", "type": "acronym"}
        }
    
    def add_entry(self, word: str, pronunciation: str, word_type: str = "default", 
                  language: str = "en", priority: int = 0) -> bool:
        """
        Add a pronunciation entry to the dictionary.
        
        Args:
            word: The word to add
            pronunciation: The pronunciation of the word
            word_type: Type of word (acronym, technical, etc.)
            language: Language code
            priority: Priority level (higher numbers take precedence)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            self.dictionary[word] = {
                "pronunciation": pronunciation,
                "type": word_type,
                "language": language,
                "priority": priority
            }
            return True
        except Exception as e:
            logging.error(f"Failed to add dictionary entry: {e}")
            return False
    
    def remove_entry(self, word: str) -> bool:
        """
        Remove a pronunciation entry from the dictionary.
        
        Args:
            word: The word to remove
            
        Returns:
            True if successful, False otherwise
        """
        try:
            if word in self.dictionary:
                del self.dictionary[word]
                return True
            return False
        except Exception as e:
            logging.error(f"Failed to remove dictionary entry: {e}")
            return False
    
    def get_pronunciation(self, word: str, language: str = "en") -> Optional[str]:
        """
        Get the pronunciation for a word.
        
        Args:
            word: The word to look up
            language: Language code
            
        Returns:
            Pronunciation string or None if not found
        """
        # Check exact match
        if word in self.dictionary:
            entry = self.dictionary[word]
            if entry.get("language", "en") == language:
                return entry["pronunciation"]
        
        # Check case-insensitive match
        word_lower = word.lower()
        for dict_word, entry in self.dictionary.items():
            if dict_word.lower() == word_lower and entry.get("language", "en") == language:
                return entry["pronunciation"]
        
        return None
    
    def apply_pronunciation(self, text: str, language: str = "en") -> str:
        """
        Apply pronunciation rules to text.
        
        Args:
            text: Text to process
            language: Language code
            
        Returns:
            Text with pronunciation applied
        """
        # Sort words by length (longest first) to avoid partial matches
        sorted_words = sorted(self.dictionary.keys(), key=len, reverse=True)
        
        # Apply pronunciation rules
        for word in sorted_words:
            pronunciation = self.get_pronunciation(word, language)
            if pronunciation:
                # Replace word with pronunciation, but be careful about word boundaries
                pattern = r'\b' + re.escape(word) + r'\b'
                text = re.sub(pattern, pronunciation, text, flags=re.IGNORECASE)
        
        return text
    
    def get_entries_by_type(self, word_type: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all entries of a specific type.
        
        Args:
            word_type: Type of words to retrieve
            
        Returns:
            Dictionary of entries
        """
        return {word: entry for word, entry in self.dictionary.items() 
                if entry.get("type") == word_type}
    
    def get_entries_by_language(self, language: str) -> Dict[str, Dict[str, Any]]:
        """
        Get all entries for a specific language.
        
        Args:
            language: Language code
            
        Returns:
            Dictionary of entries
        """
        return {word: entry for word, entry in self.dictionary.items() 
                if entry.get("language", "en") == language}
    
    def import_from_file(self, file_path: Path) -> bool:
        """
        Import dictionary entries from a file.
        
        Args:
            file_path: Path to the file to import
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                imported_dict = json.load(f)
            
            # Merge with existing dictionary
            self.dictionary.update(imported_dict)
            return True
        except Exception as e:
            logging.error(f"Failed to import dictionary: {e}")
            return False
    
    def export_to_file(self, file_path: Path) -> bool:
        """
        Export dictionary entries to a file.
        
        Args:
            file_path: Path to the file to export to
            
        Returns:
            True if successful, False otherwise
        """
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.dictionary, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logging.error(f"Failed to export dictionary: {e}")
            return False


def main():
    """Main function for testing the pronunciation dictionary."""
    import sys
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Test pronunciation dictionary
    dict_path = Path("test_pronunciation_dict.json")
    dictionary = PronunciationDictionary(dict_path)
    
    # Test adding entries
    print("Adding test entries...")
    dictionary.add_entry("TTS", "T-T-S", "acronym")
    dictionary.add_entry("AI", "A-I", "acronym")
    dictionary.add_entry("ML", "M-L", "acronym")
    
    # Test getting pronunciation
    print("\nTesting pronunciation lookup:")
    test_words = ["CPU", "TTS", "AI", "ML", "Unknown"]
    for word in test_words:
        pronunciation = dictionary.get_pronunciation(word)
        print(f"{word}: {pronunciation}")
    
    # Test applying pronunciation to text
    print("\nTesting text processing:")
    test_text = "The CPU and GPU work with the TTS system using AI and ML."
    processed_text = dictionary.apply_pronunciation(test_text)
    print(f"Original: {test_text}")
    print(f"Processed: {processed_text}")
    
    # Save dictionary
    dictionary.save_dictionary()
    print(f"\nDictionary saved to {dict_path}")
    
    # Clean up test file
    if dict_path.exists():
        dict_path.unlink()
        print("Test file cleaned up")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())