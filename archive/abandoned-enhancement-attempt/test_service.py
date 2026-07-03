#!/usr/bin/env python3
"""
Test script for the ReadAloudTTS service.
"""

import json
import win32file
import win32pipe
import sys


def test_service():
    PIPE_NAME = r"\\.\pipe\ReadAloudTTS"
    
    try:
        # Connect to the pipe
        print("Connecting to TTS service...")
        handle = win32file.CreateFile(
            PIPE_NAME,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None
        )
        
        # Send a test request
        request = {
            "action": "speak",
            "text": "Hello, this is a test of the ReadAloud TTS service."
        }
        request_str = json.dumps(request)
        
        print("Sending request to service...")
        win32file.WriteFile(handle, request_str.encode('utf-8'))
        
        # Read response
        print("Waiting for response...")
        result, response_data = win32file.ReadFile(handle, 65536)
        response_str = response_data.decode('utf-8')
        print(f"Response: {response_str}")
        
        # Parse response
        response = json.loads(response_str)
        if response.get("status") == "success":
            print("Test successful!")
        else:
            print(f"Test failed: {response.get('message', 'Unknown error')}")
            
        win32file.CloseHandle(handle)
        
    except Exception as e:
        print(f"Test failed with error: {e}")
        return 1
        
    return 0


if __name__ == "__main__":
    sys.exit(test_service())