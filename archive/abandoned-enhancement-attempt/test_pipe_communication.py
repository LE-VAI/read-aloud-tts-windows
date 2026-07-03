import json
import win32file
import win32pipe
import sys
import time

def test_named_pipe_communication():
    PIPE_NAME = r"\\.\pipe\ReadAloudTTS"
    
    print("Testing named pipe communication with ReadAloudTTS service...")
    
    try:
        # Try to connect to the pipe
        print("Attempting to connect to pipe...")
        handle = win32file.CreateFile(
            PIPE_NAME,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None
        )
        print("Connected to pipe successfully!")
        
        # Send a simple get_voice request
        request = {"action": "get_voice"}
        request_str = json.dumps(request)
        
        print("Sending get_voice request...")
        win32file.WriteFile(handle, request_str.encode('utf-8'))
        
        # Read response
        print("Waiting for response...")
        result, response_data = win32file.ReadFile(handle, 65536)
        response_str = response_data.decode('utf-8')
        print(f"Response received: {response_str}")
        
        # Parse response
        response = json.loads(response_str)
        if response.get("status") == "success":
            print("Named pipe communication test PASSED!")
            voice_id = response.get("voice_id", "unknown")
            print(f"Current voice: {voice_id}")
        else:
            print(f"Test failed: {response.get('message', 'Unknown error')}")
            
        win32file.CloseHandle(handle)
        return 0
        
    except Exception as e:
        print(f"Named pipe communication test FAILED with error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(test_named_pipe_communication())