"""
UI components for ReadAloudTTS.
"""
import tkinter as tk
from tkinter import ttk
import threading
import time
import logging
from typing import Optional, Callable, List


class WordHighlighter:
    """Highlight words during TTS playback."""
    
    def __init__(self, parent_window=None):
        self.parent_window = parent_window
        self.highlight_window = None
        self.current_word = ""
        self.is_visible = False
        self.position = "top"  # top, bottom, left, right
        self.highlight_callback: Optional[Callable[[str], None]] = None
        self.word_list: List[str] = []
        self.current_index = 0
        self.is_playing = False
        
    def create_highlight_window(self):
        """Create the highlight window."""
        if self.highlight_window is not None:
            return
        
        # Create transparent top-level window
        self.highlight_window = tk.Toplevel(self.parent_window)
        self.highlight_window.title("Word Highlight")
        self.highlight_window.geometry("400x60+100+100")
        self.highlight_window.overrideredirect(True)  # Remove window decorations
        self.highlight_window.attributes("-topmost", True)  # Keep on top
        self.highlight_window.attributes("-transparentcolor", "white")  # Make white transparent
        self.highlight_window.configure(bg="white")
        
        # Create frame with semi-transparent background
        self.highlight_frame = tk.Frame(
            self.highlight_window, 
            bg="#222222", 
            relief="raised", 
            bd=1
        )
        self.highlight_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # Create label for highlighted word
        self.word_label = tk.Label(
            self.highlight_frame,
            text="",
            font=("Arial", 14, "bold"),
            fg="#FFFFFF",
            bg="#222222"
        )
        self.word_label.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
        
        # Bind events for window dragging
        self.highlight_frame.bind("<Button-1>", self.start_drag)
        self.highlight_frame.bind("<B1-Motion>", self.drag_window)
        self.word_label.bind("<Button-1>", self.start_drag)
        self.word_label.bind("<B1-Motion>", self.drag_window)
        
        self.drag_data = {"x": 0, "y": 0}
        
        # Hide initially
        self.hide()
    
    def start_drag(self, event):
        """Start dragging the window."""
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
    
    def drag_window(self, event):
        """Drag the window."""
        x = self.highlight_window.winfo_x() - self.drag_data["x"] + event.x
        y = self.highlight_window.winfo_y() - self.drag_data["y"] + event.y
        self.highlight_window.geometry(f"+{x}+{y}")
    
    def show(self):
        """Show the highlight window."""
        if self.highlight_window is None:
            self.create_highlight_window()
        
        if not self.is_visible:
            self.highlight_window.deiconify()
            self.is_visible = True
    
    def hide(self):
        """Hide the highlight window."""
        if self.highlight_window is not None and self.is_visible:
            self.highlight_window.withdraw()
            self.is_visible = False
    
    def set_word(self, word: str):
        """Set the current word to highlight."""
        self.current_word = word
        if self.word_label is not None:
            self.word_label.config(text=word)
    
    def set_word_list(self, words: List[str]):
        """Set the list of words to highlight."""
        self.word_list = words
        self.current_index = 0
    
    def highlight_next_word(self):
        """Highlight the next word in the list."""
        if self.current_index < len(self.word_list):
            word = self.word_list[self.current_index]
            self.set_word(word)
            self.current_index += 1
            
            # Call callback if set
            if self.highlight_callback:
                self.highlight_callback(word)
    
    def start_highlighting(self, words: List[str], delay: float = 0.5):
        """
        Start highlighting words.
        
        Args:
            words: List of words to highlight
            delay: Delay between words in seconds
        """
        self.set_word_list(words)
        self.is_playing = True
        self.show()
        
        def highlight_worker():
            for word in words:
                if not self.is_playing:
                    break
                self.set_word(word)
                if self.highlight_callback:
                    self.highlight_callback(word)
                time.sleep(delay)
            
            # Hide window when done
            self.hide()
            self.is_playing = False
        
        # Start highlighting in a separate thread
        thread = threading.Thread(target=highlight_worker, daemon=True)
        thread.start()
    
    def stop_highlighting(self):
        """Stop highlighting words."""
        self.is_playing = False
        self.hide()
    
    def set_position(self, position: str):
        """
        Set the position of the highlight window.
        
        Args:
            position: "top", "bottom", "left", or "right"
        """
        self.position = position
        # Implementation would adjust window geometry based on position
    
    def set_callback(self, callback: Callable[[str], None]):
        """
        Set callback function for word highlighting.
        
        Args:
            callback: Function to call when a word is highlighted
        """
        self.highlight_callback = callback


class HoverControls:
    """Hover controls for TTS playback."""
    
    def __init__(self, parent_window=None):
        self.parent_window = parent_window
        self.controls_window = None
        self.is_visible = False
        self.timeout_timer = None
        self.hide_timeout = 3.0  # Hide after 3 seconds of inactivity
        
    def create_controls_window(self):
        """Create the controls window."""
        if self.controls_window is not None:
            return
        
        # Create transparent top-level window
        self.controls_window = tk.Toplevel(self.parent_window)
        self.controls_window.title("TTS Controls")
        self.controls_window.geometry("200x50+200+200")
        self.controls_window.overrideredirect(True)  # Remove window decorations
        self.controls_window.attributes("-topmost", True)  # Keep on top
        self.controls_window.configure(bg="#222222")
        
        # Create frame for controls
        controls_frame = tk.Frame(self.controls_window, bg="#222222")
        controls_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create control buttons
        self.pause_button = tk.Button(
            controls_frame,
            text="⏸",
            font=("Arial", 12),
            width=3,
            command=self.on_pause,
            bg="#444444",
            fg="#FFFFFF",
            relief="flat",
            activebackground="#555555"
        )
        self.pause_button.pack(side=tk.LEFT, padx=2)
        
        self.rewind_button = tk.Button(
            controls_frame,
            text="⏪",
            font=("Arial", 12),
            width=3,
            command=self.on_rewind,
            bg="#444444",
            fg="#FFFFFF",
            relief="flat",
            activebackground="#555555"
        )
        self.rewind_button.pack(side=tk.LEFT, padx=2)
        
        self.forward_button = tk.Button(
            controls_frame,
            text="⏩",
            font=("Arial", 12),
            width=3,
            command=self.on_forward,
            bg="#444444",
            fg="#FFFFFF",
            relief="flat",
            activebackground="#555555"
        )
        self.forward_button.pack(side=tk.LEFT, padx=2)
        
        self.stop_button = tk.Button(
            controls_frame,
            text="⏹",
            font=("Arial", 12),
            width=3,
            command=self.on_stop,
            bg="#444444",
            fg="#FFFFFF",
            relief="flat",
            activebackground="#555555"
        )
        self.stop_button.pack(side=tk.LEFT, padx=2)
        
        # Bind mouse events for auto-hide
        for widget in [self.controls_window, controls_frame, 
                      self.pause_button, self.rewind_button, 
                      self.forward_button, self.stop_button]:
            widget.bind("<Enter>", self.on_mouse_enter)
            widget.bind("<Leave>", self.on_mouse_leave)
    
    def on_mouse_enter(self, event):
        """Handle mouse enter event."""
        # Cancel hide timer
        if self.timeout_timer:
            self.timeout_timer.cancel()
            self.timeout_timer = None
    
    def on_mouse_leave(self, event):
        """Handle mouse leave event."""
        # Start hide timer
        self.start_hide_timer()
    
    def start_hide_timer(self):
        """Start the timer to hide the controls."""
        if self.timeout_timer:
            self.timeout_timer.cancel()
        
        self.timeout_timer = threading.Timer(self.hide_timeout, self.hide)
        self.timeout_timer.start()
    
    def show(self, x: int = None, y: int = None):
        """Show the controls window."""
        if self.controls_window is None:
            self.create_controls_window()
        
        if not self.is_visible:
            # Position window if coordinates provided
            if x is not None and y is not None:
                self.controls_window.geometry(f"+{x}+{y}")
            
            self.controls_window.deiconify()
            self.is_visible = True
            # Start hide timer
            self.start_hide_timer()
    
    def hide(self):
        """Hide the controls window."""
        if self.controls_window is not None and self.is_visible:
            self.controls_window.withdraw()
            self.is_visible = False
            # Cancel timer
            if self.timeout_timer:
                self.timeout_timer.cancel()
                self.timeout_timer = None
    
    def on_pause(self):
        """Handle pause button click."""
        logging.info("Pause button clicked")
        # Implementation would pause TTS playback
    
    def on_rewind(self):
        """Handle rewind button click."""
        logging.info("Rewind button clicked")
        # Implementation would rewind TTS playback
    
    def on_forward(self):
        """Handle forward button click."""
        logging.info("Forward button clicked")
        # Implementation would forward TTS playback
    
    def on_stop(self):
        """Handle stop button click."""
        logging.info("Stop button clicked")
        # Implementation would stop TTS playback


class TranscriptOverlay:
    """Minimal transcript overlay that doesn't interfere with the screen."""
    
    def __init__(self, parent_window=None):
        self.parent_window = parent_window
        self.overlay_window = None
        self.is_visible = False
        self.transcript_text = ""
        self.position = "bottom"  # top, bottom, left, right
        self.transparency = 0.8  # 0.0 (transparent) to 1.0 (opaque)
        self.auto_hide = True
        self.hide_timeout = 5.0  # Hide after 5 seconds of inactivity
        self.timeout_timer = None
        
    def create_overlay_window(self):
        """Create the transcript overlay window."""
        if self.overlay_window is not None:
            return
        
        # Create transparent top-level window
        self.overlay_window = tk.Toplevel(self.parent_window)
        self.overlay_window.title("Transcript Overlay")
        self.overlay_window.geometry("600x100+100+100")
        self.overlay_window.overrideredirect(True)  # Remove window decorations
        self.overlay_window.attributes("-topmost", True)  # Keep on top
        self.overlay_window.attributes("-alpha", self.transparency)  # Set transparency
        self.overlay_window.configure(bg="#111111")
        
        # Create frame with semi-transparent background
        self.overlay_frame = tk.Frame(
            self.overlay_window, 
            bg="#111111", 
            relief="raised", 
            bd=1
        )
        self.overlay_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # Create text widget for transcript
        self.transcript_text_widget = tk.Text(
            self.overlay_frame,
            font=("Arial", 10),
            fg="#EEEEEE",
            bg="#111111",
            wrap=tk.WORD,
            state=tk.DISABLED,
            highlightthickness=0,
            borderwidth=0
        )
        self.transcript_text_widget.pack(expand=True, fill=tk.BOTH, padx=5, pady=5)
        
        # Add scrollbar
        scrollbar = tk.Scrollbar(self.overlay_frame, command=self.transcript_text_widget.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.transcript_text_widget.config(yscrollcommand=scrollbar.set)
        
        # Bind events for window dragging and auto-hide
        for widget in [self.overlay_window, self.overlay_frame, self.transcript_text_widget]:
            widget.bind("<Button-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.drag_window)
            widget.bind("<Enter>", self.on_mouse_enter)
            widget.bind("<Leave>", self.on_mouse_leave)
        
        self.drag_data = {"x": 0, "y": 0}
        
        # Hide initially
        self.hide()
    
    def start_drag(self, event):
        """Start dragging the window."""
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
    
    def drag_window(self, event):
        """Drag the window."""
        x = self.overlay_window.winfo_x() - self.drag_data["x"] + event.x
        y = self.overlay_window.winfo_y() - self.drag_data["y"] + event.y
        self.overlay_window.geometry(f"+{x}+{y}")
    
    def on_mouse_enter(self, event):
        """Handle mouse enter event."""
        # Cancel hide timer
        if self.timeout_timer:
            self.timeout_timer.cancel()
            self.timeout_timer = None
    
    def on_mouse_leave(self, event):
        """Handle mouse leave event."""
        # Start hide timer if auto-hide is enabled
        if self.auto_hide:
            self.start_hide_timer()
    
    def start_hide_timer(self):
        """Start the timer to hide the overlay."""
        if self.timeout_timer:
            self.timeout_timer.cancel()
        
        self.timeout_timer = threading.Timer(self.hide_timeout, self.hide)
        self.timeout_timer.start()
    
    def show(self):
        """Show the transcript overlay."""
        if self.overlay_window is None:
            self.create_overlay_window()
        
        if not self.is_visible:
            self.overlay_window.deiconify()
            self.is_visible = True
            # Start hide timer if auto-hide is enabled
            if self.auto_hide:
                self.start_hide_timer()
    
    def hide(self):
        """Hide the transcript overlay."""
        if self.overlay_window is not None and self.is_visible:
            self.overlay_window.withdraw()
            self.is_visible = False
            # Cancel timer
            if self.timeout_timer:
                self.timeout_timer.cancel()
                self.timeout_timer = None
    
    def set_transcript(self, text: str):
        """Set the transcript text."""
        self.transcript_text = text
        if self.transcript_text_widget is not None:
            self.transcript_text_widget.config(state=tk.NORMAL)
            self.transcript_text_widget.delete(1.0, tk.END)
            self.transcript_text_widget.insert(tk.END, text)
            self.transcript_text_widget.config(state=tk.DISABLED)
            # Scroll to the end
            self.transcript_text_widget.see(tk.END)
    
    def append_text(self, text: str):
        """Append text to the transcript."""
        self.transcript_text += text
        if self.transcript_text_widget is not None:
            self.transcript_text_widget.config(state=tk.NORMAL)
            self.transcript_text_widget.insert(tk.END, text)
            self.transcript_text_widget.config(state=tk.DISABLED)
            # Scroll to the end
            self.transcript_text_widget.see(tk.END)
    
    def set_position(self, position: str):
        """
        Set the position of the overlay window.
        
        Args:
            position: "top", "bottom", "left", or "right"
        """
        self.position = position
        # Implementation would adjust window geometry based on position
    
    def set_transparency(self, transparency: float):
        """
        Set the transparency of the overlay window.
        
        Args:
            transparency: 0.0 (transparent) to 1.0 (opaque)
        """
        self.transparency = max(0.0, min(1.0, transparency))
        if self.overlay_window is not None:
            self.overlay_window.attributes("-alpha", self.transparency)
    
    def set_auto_hide(self, auto_hide: bool):
        """
        Set whether the overlay should auto-hide.
        
        Args:
            auto_hide: True to enable auto-hide, False to disable
        """
        self.auto_hide = auto_hide
        if not auto_hide and self.timeout_timer:
            self.timeout_timer.cancel()
            self.timeout_timer = None


class ProgressBar:
    """Visual progress bar showing reading position."""
    
    def __init__(self, parent_window=None):
        self.parent_window = parent_window
        self.progress_window = None
        self.is_visible = False
        self.position = "bottom"  # top, bottom
        self.transparency = 0.9  # 0.0 (transparent) to 1.0 (opaque)
        self.current_progress = 0.0  # 0.0 to 1.0
        self.total_duration = 0.0  # Total duration in seconds
        self.elapsed_time = 0.0  # Elapsed time in seconds
        self.is_playing = False
        
    def create_progress_window(self):
        """Create the progress bar window."""
        if self.progress_window is not None:
            return
        
        # Create transparent top-level window
        self.progress_window = tk.Toplevel(self.parent_window)
        self.progress_window.title("Progress Bar")
        self.progress_window.geometry("400x30+100+100")
        self.progress_window.overrideredirect(True)  # Remove window decorations
        self.progress_window.attributes("-topmost", True)  # Keep on top
        self.progress_window.attributes("-alpha", self.transparency)  # Set transparency
        self.progress_window.configure(bg="#111111")
        
        # Create frame with semi-transparent background
        self.progress_frame = tk.Frame(
            self.progress_window, 
            bg="#111111", 
            relief="raised", 
            bd=1
        )
        self.progress_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        
        # Create progress bar
        self.progress_bar = ttk.Progressbar(
            self.progress_frame,
            orient=tk.HORIZONTAL,
            length=380,
            mode='determinate'
        )
        self.progress_bar.pack(side=tk.TOP, fill=tk.X, padx=5, pady=(5, 2))
        
        # Create time labels
        time_frame = tk.Frame(self.progress_frame, bg="#111111")
        time_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=(0, 5))
        
        self.elapsed_label = tk.Label(
            time_frame,
            text="00:00",
            font=("Arial", 8),
            fg="#CCCCCC",
            bg="#111111"
        )
        self.elapsed_label.pack(side=tk.LEFT)
        
        self.separator_label = tk.Label(
            time_frame,
            text=" / ",
            font=("Arial", 8),
            fg="#CCCCCC",
            bg="#111111"
        )
        self.separator_label.pack(side=tk.LEFT)
        
        self.total_label = tk.Label(
            time_frame,
            text="00:00",
            font=("Arial", 8),
            fg="#CCCCCC",
            bg="#111111"
        )
        self.total_label.pack(side=tk.LEFT)
        
        # Bind events for window dragging
        for widget in [self.progress_window, self.progress_frame, self.progress_bar]:
            widget.bind("<Button-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.drag_window)
        
        self.drag_data = {"x": 0, "y": 0}
        
        # Hide initially
        self.hide()
    
    def start_drag(self, event):
        """Start dragging the window."""
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
    
    def drag_window(self, event):
        """Drag the window."""
        x = self.progress_window.winfo_x() - self.drag_data["x"] + event.x
        y = self.progress_window.winfo_y() - self.drag_data["y"] + event.y
        self.progress_window.geometry(f"+{x}+{y}")
    
    def show(self):
        """Show the progress bar window."""
        if self.progress_window is None:
            self.create_progress_window()
        
        if not self.is_visible:
            self.progress_window.deiconify()
            self.is_visible = True
    
    def hide(self):
        """Hide the progress bar window."""
        if self.progress_window is not None and self.is_visible:
            self.progress_window.withdraw()
            self.is_visible = False
    
    def set_progress(self, progress: float):
        """
        Set the progress value.
        
        Args:
            progress: Progress value from 0.0 to 1.0
        """
        self.current_progress = max(0.0, min(1.0, progress))
        if self.progress_bar is not None:
            self.progress_bar['value'] = self.current_progress * 100
    
    def set_time(self, elapsed: float, total: float):
        """
        Set the elapsed and total time.
        
        Args:
            elapsed: Elapsed time in seconds
            total: Total time in seconds
        """
        self.elapsed_time = elapsed
        self.total_duration = total
        
        if self.elapsed_label is not None and self.total_label is not None:
            # Format time as MM:SS
            elapsed_minutes = int(elapsed // 60)
            elapsed_seconds = int(elapsed % 60)
            total_minutes = int(total // 60)
            total_seconds = int(total % 60)
            
            elapsed_text = f"{elapsed_minutes:02d}:{elapsed_seconds:02d}"
            total_text = f"{total_minutes:02d}:{total_seconds:02d}"
            
            self.elapsed_label.config(text=elapsed_text)
            self.total_label.config(text=total_text)
    
    def start_progress(self, total_duration: float):
        """
        Start progress tracking.
        
        Args:
            total_duration: Total duration in seconds
        """
        self.total_duration = total_duration
        self.elapsed_time = 0.0
        self.is_playing = True
        self.show()
        
        # Update time labels
        self.set_time(0.0, total_duration)
        
        # Start progress update thread
        def progress_worker():
            start_time = time.time()
            while self.is_playing and self.elapsed_time < self.total_duration:
                time.sleep(0.1)  # Update every 100ms
                self.elapsed_time = time.time() - start_time
                progress = self.elapsed_time / self.total_duration if self.total_duration > 0 else 0
                self.set_progress(progress)
                self.set_time(self.elapsed_time, self.total_duration)
        
        thread = threading.Thread(target=progress_worker, daemon=True)
        thread.start()
    
    def stop_progress(self):
        """Stop progress tracking."""
        self.is_playing = False
        self.hide()
    
    def set_position(self, position: str):
        """
        Set the position of the progress window.
        
        Args:
            position: "top" or "bottom"
        """
        self.position = position
        # Implementation would adjust window geometry based on position
    
    def set_transparency(self, transparency: float):
        """
        Set the transparency of the progress window.
        
        Args:
            transparency: 0.0 (transparent) to 1.0 (opaque)
        """
        self.transparency = max(0.0, min(1.0, transparency))
        if self.progress_window is not None:
            self.progress_window.attributes("-alpha", self.transparency)


def main():
    """Main function for testing UI components."""
    import sys
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Create root window
    root = tk.Tk()
    root.title("UI Components Test")
    root.geometry("400x300")
    
    # Create word highlighter
    highlighter = WordHighlighter(root)
    
    # Create hover controls
    controls = HoverControls(root)
    
    # Create transcript overlay
    overlay = TranscriptOverlay(root)
    
    # Create progress bar
    progress = ProgressBar(root)
    
    # Test functions
    def test_highlight():
        words = ["This", "is", "a", "test", "of", "the", "word", "highlighting", "system"]
        highlighter.start_highlighting(words, delay=0.8)
    
    def test_controls():
        controls.show(300, 300)
    
    def test_overlay():
        overlay.set_transcript("This is a test of the transcript overlay system. It should display text in a non-intrusive way that doesn't interfere with the underlying application.")
        overlay.show()
    
    def test_progress():
        progress.start_progress(10.0)  # 10 seconds total
    
    # Create test buttons
    tk.Button(root, text="Test Highlighting", command=test_highlight).pack(pady=5)
    tk.Button(root, text="Test Controls", command=test_controls).pack(pady=5)
    tk.Button(root, text="Test Overlay", command=test_overlay).pack(pady=5)
    tk.Button(root, text="Test Progress", command=test_progress).pack(pady=5)
    tk.Button(root, text="Hide Controls", command=controls.hide).pack(pady=5)
    tk.Button(root, text="Hide Overlay", command=overlay.hide).pack(pady=5)
    tk.Button(root, text="Hide Progress", command=progress.hide).pack(pady=5)
    
    # Hide windows initially
    root.after(100, highlighter.hide)
    root.after(100, controls.hide)
    root.after(100, overlay.hide)
    root.after(100, progress.hide)
    
    # Start main loop
    root.mainloop()
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())