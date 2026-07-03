"""
Configuration UI for ReadAloudTTS.
"""
import tkinter as tk
from tkinter import ttk, messagebox
import json
from pathlib import Path
import logging


class ConfigUI:
    """Configuration UI for ReadAloudTTS."""
    
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.root = tk.Tk()
        self.root.title("ReadAloudTTS Configuration")
        self.root.geometry("500x600")
        self.root.resizable(True, True)
        
        # Load current config
        self.config = self.load_config()
        
        # Create UI elements
        self.create_widgets()
        
        # Load current values
        self.load_values()
    
    def load_config(self) -> dict:
        """Load configuration from file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Failed to load config: {e}")
            return {}
    
    def save_config(self) -> bool:
        """Save configuration to file."""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logging.error(f"Failed to save config: {e}")
            messagebox.showerror("Error", f"Failed to save configuration: {e}")
            return False
    
    def create_widgets(self):
        """Create UI widgets."""
        # Create notebook for tabs
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # General tab
        general_frame = ttk.Frame(notebook)
        notebook.add(general_frame, text="General")
        self.create_general_tab(general_frame)
        
        # Voice tab
        voice_frame = ttk.Frame(notebook)
        notebook.add(voice_frame, text="Voice")
        self.create_voice_tab(voice_frame)
        
        # Engine tab
        engine_frame = ttk.Frame(notebook)
        notebook.add(engine_frame, text="Engine")
        self.create_engine_tab(engine_frame)
        
        # Prosody tab
        prosody_frame = ttk.Frame(notebook)
        notebook.add(prosody_frame, text="Prosody")
        self.create_prosody_tab(prosody_frame)
        
        # UI tab
        ui_frame = ttk.Frame(notebook)
        notebook.add(ui_frame, text="UI")
        self.create_ui_tab(ui_frame)
        
        # Buttons frame
        button_frame = ttk.Frame(self.root)
        button_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Save button
        save_button = ttk.Button(button_frame, text="Save", command=self.save_settings)
        save_button.pack(side=tk.RIGHT, padx=5)
        
        # Cancel button
        cancel_button = ttk.Button(button_frame, text="Cancel", command=self.root.destroy)
        cancel_button.pack(side=tk.RIGHT, padx=5)
        
        # Apply button
        apply_button = ttk.Button(button_frame, text="Apply", command=self.apply_settings)
        apply_button.pack(side=tk.RIGHT, padx=5)
    
    def create_general_tab(self, parent):
        """Create general settings tab."""
        # Max characters
        ttk.Label(parent, text="Max Characters:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.max_chars_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.max_chars_var, width=20).grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Chunk characters
        ttk.Label(parent, text="Chunk Characters:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.chunk_chars_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.chunk_chars_var, width=20).grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        
        # Add padding
        for i in range(2):
            parent.grid_rowconfigure(i, weight=0)
        parent.grid_rowconfigure(2, weight=1)
    
    def create_voice_tab(self, parent):
        """Create voice settings tab."""
        # Current voice
        ttk.Label(parent, text="Current Voice:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.current_voice_var = tk.StringVar()
        voice_combo = ttk.Combobox(parent, textvariable=self.current_voice_var, width=30, state="readonly")
        
        # Populate voices
        voices = self.config.get("voices", {})
        voice_options = [f"{vid} - {v.get('label', vid)}" for vid, v in voices.items()]
        voice_ids = list(voices.keys())
        
        voice_combo['values'] = voice_options
        voice_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        self.voice_combo = voice_combo
        self.voice_ids = voice_ids
        
        # Add padding
        for i in range(1):
            parent.grid_rowconfigure(i, weight=0)
        parent.grid_rowconfigure(1, weight=1)
    
    def create_engine_tab(self, parent):
        """Create engine settings tab."""
        # Default engine
        ttk.Label(parent, text="Default Engine:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.default_engine_var = tk.StringVar()
        engine_combo = ttk.Combobox(parent, textvariable=self.default_engine_var, width=30, state="readonly")
        
        # Populate engines
        engines = self.config.get("engines", {})
        engine_options = [f"{eid} - {e.get('label', eid)}" for eid, e in engines.items()]
        engine_ids = list(engines.keys())
        
        engine_combo['values'] = engine_options
        engine_combo.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        self.engine_combo = engine_combo
        self.engine_ids = engine_ids
        
        # Add padding
        for i in range(1):
            parent.grid_rowconfigure(i, weight=0)
        parent.grid_rowconfigure(1, weight=1)
    
    def create_prosody_tab(self, parent):
        """Create prosody settings tab."""
        # Pitch
        ttk.Label(parent, text="Pitch (0-99):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.pitch_var = tk.StringVar()
        pitch_frame = ttk.Frame(parent)
        pitch_frame.grid(row=0, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(pitch_frame, textvariable=self.pitch_var, width=10).pack(side=tk.LEFT)
        self.pitch_scale = ttk.Scale(pitch_frame, from_=0, to=99, orient=tk.HORIZONTAL, 
                                     variable=self.pitch_var, length=200)
        self.pitch_scale.pack(side=tk.LEFT, padx=(5, 0))
        
        # Speed
        ttk.Label(parent, text="Speed (0.5-2.0):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.speed_var = tk.StringVar()
        speed_frame = ttk.Frame(parent)
        speed_frame.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(speed_frame, textvariable=self.speed_var, width=10).pack(side=tk.LEFT)
        self.speed_scale = ttk.Scale(speed_frame, from_=0.5, to=2.0, orient=tk.HORIZONTAL, 
                                     variable=self.speed_var, length=200)
        self.speed_scale.pack(side=tk.LEFT, padx=(5, 0))
        
        # Volume
        ttk.Label(parent, text="Volume (0-100):").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        self.volume_var = tk.StringVar()
        volume_frame = ttk.Frame(parent)
        volume_frame.grid(row=2, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(volume_frame, textvariable=self.volume_var, width=10).pack(side=tk.LEFT)
        self.volume_scale = ttk.Scale(volume_frame, from_=0, to=100, orient=tk.HORIZONTAL, 
                                      variable=self.volume_var, length=200)
        self.volume_scale.pack(side=tk.LEFT, padx=(5, 0))
        
        # Add padding
        for i in range(3):
            parent.grid_rowconfigure(i, weight=0)
        parent.grid_rowconfigure(3, weight=1)
    
    def create_ui_tab(self, parent):
        """Create UI settings tab."""
        ttk.Label(parent, text="UI settings will be implemented in future versions.").pack(padx=10, pady=10)
        
        # Add padding
        parent.grid_rowconfigure(0, weight=1)
    
    def load_values(self):
        """Load current values from config."""
        # General settings
        self.max_chars_var.set(str(self.config.get("max_chars", 30000)))
        self.chunk_chars_var.set(str(self.config.get("chunk_chars", 900)))
        
        # Voice settings
        current_voice = self.config.get("current_voice", "en_US-lessac-medium")
        if current_voice in self.voice_ids:
            idx = self.voice_ids.index(current_voice)
            self.current_voice_var.set(self.voice_combo['values'][idx])
        
        # Engine settings
        default_engine = self.config.get("default_engine", "piper")
        if default_engine in self.engine_ids:
            idx = self.engine_ids.index(default_engine)
            self.default_engine_var.set(self.engine_combo['values'][idx])
        
        # Prosody settings
        self.pitch_var.set(str(self.config.get("pitch", 50)))
        self.speed_var.set(str(self.config.get("speed", 1.0)))
        self.volume_var.set(str(self.config.get("volume", 100)))
        
        # Update scales
        try:
            self.pitch_scale.set(float(self.pitch_var.get()))
            self.speed_scale.set(float(self.speed_var.get()))
            self.volume_scale.set(float(self.volume_var.get()))
        except ValueError:
            pass
    
    def save_settings(self):
        """Save settings to config file."""
        if self.apply_settings():
            self.root.destroy()
    
    def apply_settings(self):
        """Apply settings to config."""
        try:
            # General settings
            self.config["max_chars"] = int(self.max_chars_var.get())
            self.config["chunk_chars"] = int(self.chunk_chars_var.get())
            
            # Voice settings
            voice_selection = self.current_voice_var.get()
            if voice_selection and " - " in voice_selection:
                voice_id = voice_selection.split(" - ")[0]
                self.config["current_voice"] = voice_id
            
            # Engine settings
            engine_selection = self.default_engine_var.get()
            if engine_selection and " - " in engine_selection:
                engine_id = engine_selection.split(" - ")[0]
                self.config["default_engine"] = engine_id
            
            # Prosody settings
            self.config["pitch"] = int(float(self.pitch_var.get()))
            self.config["speed"] = float(self.speed_var.get())
            self.config["volume"] = int(float(self.volume_var.get()))
            
            # Save config
            if self.save_config():
                messagebox.showinfo("Success", "Configuration saved successfully!")
                return True
            else:
                return False
        except Exception as e:
            messagebox.showerror("Error", f"Failed to apply settings: {e}")
            return False
    
    def run(self):
        """Run the configuration UI."""
        self.root.mainloop()


def main():
    """Main function for testing the configuration UI."""
    import argparse
    import sys
    import os
    
    # Add the parent directory to the path so we can import the tts module
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    parser = argparse.ArgumentParser(description="ReadAloudTTS Configuration UI")
    parser.add_argument("--config", default="config.json", help="Path to config file")
    args = parser.parse_args()
    
    config_path = Path(args.config)
    if not config_path.exists():
        # Try to find config in parent directory
        config_path = Path(__file__).parent.parent / "config.json"
        if not config_path.exists():
            print(f"Config file not found: {args.config}")
            return 1
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Run the UI
    ui = ConfigUI(config_path)
    ui.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())