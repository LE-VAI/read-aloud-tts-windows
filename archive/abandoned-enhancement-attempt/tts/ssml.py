"""
SSML (Speech Synthesis Markup Language) support for ReadAloudTTS.
"""
import re
import logging
from typing import Dict, Any, Optional
from xml.etree import ElementTree as ET


class SSMLProcessor:
    """Process SSML markup for TTS engines."""
    
    def __init__(self):
        self.supported_tags = {
            'speak', 'voice', 'prosody', 'emphasis', 'break', 
            'say-as', 'phoneme', 'sub', 'mark', 'audio', 'desc'
        }
    
    def parse_ssml(self, ssml_text: str) -> Dict[str, Any]:
        """
        Parse SSML text and extract prosody parameters.
        
        Args:
            ssml_text: SSML markup text
            
        Returns:
            Dictionary with extracted parameters
        """
        try:
            # Parse XML
            root = ET.fromstring(ssml_text)
            
            # Extract parameters
            params = {}
            
            # Process prosody tags
            for prosody in root.iter('prosody'):
                if 'pitch' in prosody.attrib:
                    params['pitch'] = self._parse_pitch(prosody.attrib['pitch'])
                if 'rate' in prosody.attrib:
                    params['speed'] = self._parse_rate(prosody.attrib['rate'])
                if 'volume' in prosody.attrib:
                    params['volume'] = self._parse_volume(prosody.attrib['volume'])
            
            # Process voice tags
            for voice in root.iter('voice'):
                if 'name' in voice.attrib:
                    params['voice'] = voice.attrib['name']
            
            # Extract plain text content
            params['text'] = self._extract_text(root)
            
            return params
        except ET.ParseError as e:
            logging.error(f"Failed to parse SSML: {e}")
            # Return original text if parsing fails
            return {'text': ssml_text}
        except Exception as e:
            logging.error(f"Error processing SSML: {e}")
            return {'text': ssml_text}
    
    def _parse_pitch(self, pitch: str) -> Optional[int]:
        """Parse pitch value."""
        try:
            # Handle relative values
            if pitch.startswith('+') or pitch.startswith('-'):
                # For now, we'll return None for relative values
                # A more sophisticated implementation would adjust the current pitch
                return None
            elif pitch.endswith('%'):
                # Percentage values
                percent = float(pitch.rstrip('%'))
                # Convert to 0-99 range (assuming 100% is default pitch of 50)
                return max(0, min(99, int(50 + (percent / 100) * 50)))
            else:
                # Absolute values - try to convert to integer
                return max(0, min(99, int(float(pitch))))
        except ValueError:
            return None
    
    def _parse_rate(self, rate: str) -> Optional[float]:
        """Parse rate value."""
        try:
            # Handle relative values
            if rate.startswith('+') or rate.startswith('-'):
                # For now, we'll return None for relative values
                return None
            elif rate.endswith('%'):
                # Percentage values
                percent = float(rate.rstrip('%'))
                # Convert to 0.5-2.0 range (assuming 100% is default rate of 1.0)
                return max(0.5, min(2.0, percent / 100.0))
            else:
                # Absolute values
                return max(0.5, min(2.0, float(rate)))
        except ValueError:
            return None
    
    def _parse_volume(self, volume: str) -> Optional[int]:
        """Parse volume value."""
        try:
            # Handle relative values
            if volume.startswith('+') or volume.startswith('-'):
                # For now, we'll return None for relative values
                return None
            elif volume.endswith('%'):
                # Percentage values
                percent = float(volume.rstrip('%'))
                # Convert to 0-100 range
                return max(0, min(100, int(percent)))
            else:
                # Absolute values
                return max(0, min(100, int(float(volume))))
        except ValueError:
            return None
    
    def _extract_text(self, element: ET.Element) -> str:
        """Extract plain text from XML element."""
        # Get text content
        text_parts = []
        
        if element.text:
            text_parts.append(element.text)
        
        for child in element:
            # Recursively extract text from child elements
            text_parts.append(self._extract_text(child))
            
            if child.tail:
                text_parts.append(child.tail)
        
        return ''.join(text_parts)
    
    def generate_ssml(self, text: str, **kwargs) -> str:
        """
        Generate SSML markup from text and parameters.
        
        Args:
            text: Text to speak
            **kwargs: Parameters like pitch, speed, volume, voice
            
        Returns:
            SSML markup string
        """
        # Start with root tag
        ssml_parts = ['<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">']
        
        # Add prosody tags if parameters are provided
        has_prosody = False
        prosody_attrs = []
        
        if 'pitch' in kwargs and kwargs['pitch'] is not None:
            prosody_attrs.append(f'pitch="{kwargs["pitch"]}"')
            has_prosody = True
            
        if 'speed' in kwargs and kwargs['speed'] is not None:
            prosody_attrs.append(f'rate="{kwargs["speed"]}"')
            has_prosody = True
            
        if 'volume' in kwargs and kwargs['volume'] is not None:
            prosody_attrs.append(f'volume="{kwargs["volume"]}"')
            has_prosody = True
        
        if has_prosody:
            ssml_parts.append(f'<prosody {" ".join(prosody_attrs)}>')
        
        # Add voice tag if specified
        if 'voice' in kwargs and kwargs['voice'] is not None:
            ssml_parts.append(f'<voice name="{kwargs["voice"]}">')
        
        # Add text content
        ssml_parts.append(self._escape_xml(text))
        
        # Close voice tag if opened
        if 'voice' in kwargs and kwargs['voice'] is not None:
            ssml_parts.append('</voice>')
        
        # Close prosody tag if opened
        if has_prosody:
            ssml_parts.append('</prosody>')
        
        # Close root tag
        ssml_parts.append('</speak>')
        
        return ''.join(ssml_parts)
    
    def _escape_xml(self, text: str) -> str:
        """Escape XML special characters."""
        return (text.replace('&', '&amp;')
                   .replace('<', '&lt;')
                   .replace('>', '&gt;')
                   .replace('"', '&quot;')
                   .replace("'", '&apos;'))
    
    def validate_ssml(self, ssml_text: str) -> bool:
        """
        Validate SSML markup.
        
        Args:
            ssml_text: SSML markup text
            
        Returns:
            True if valid, False otherwise
        """
        try:
            root = ET.fromstring(ssml_text)
            
            # Check root element
            if root.tag != '{http://www.w3.org/2001/10/synthesis}speak':
                # Try without namespace
                if root.tag != 'speak':
                    return False
            
            # Check for unsupported tags
            for elem in root.iter():
                tag_name = elem.tag
                # Remove namespace if present
                if '}' in tag_name:
                    tag_name = tag_name.split('}')[1]
                
                if tag_name not in self.supported_tags:
                    logging.warning(f"Unsupported SSML tag: {tag_name}")
            
            return True
        except ET.ParseError:
            return False
        except Exception:
            return False


def main():
    """Main function for testing SSML processing."""
    import sys
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Test SSML processing
    processor = SSMLProcessor()
    
    # Test parsing
    test_ssml = '''<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">
        <prosody pitch="60" rate="1.2" volume="80">
            This is a test of SSML processing.
        </prosody>
    </speak>'''
    
    print("Parsing SSML:")
    params = processor.parse_ssml(test_ssml)
    print(f"Parsed parameters: {params}")
    
    # Test generation
    print("\nGenerating SSML:")
    generated = processor.generate_ssml(
        "This is a test of SSML generation.",
        pitch=65,
        speed=1.3,
        volume=85
    )
    print(f"Generated SSML: {generated}")
    
    # Test validation
    print("\nValidating SSML:")
    is_valid = processor.validate_ssml(generated)
    print(f"Is valid: {is_valid}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())