import sys
import queue
import numpy as np
import sounddevice as sd
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QComboBox, QSlider, 
                             QPushButton, QScrollArea, QMessageBox, QGroupBox)
from PyQt6.QtCore import Qt, QTimer

# Audio Configuration
SAMPLE_RATE = 44100
BLOCK_SIZE = 1024
CHANNELS = 2

class AudioInputStrip(QWidget):
    """
    Represents a single audio input channel (e.g., Microphone, Capture Card).
    Handles its own Input Stream and Volume.
    """
    def __init__(self, parent_mixer, index):
        super().__init__()
        self.parent_mixer = parent_mixer
        self.index = index
        self.stream = None
        self.audio_queue = queue.Queue(maxsize=10) # Buffer to hold audio chunks
        self.volume = 1.0
        self.is_active = False

        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        # Group Box for visual separation
        group = QGroupBox(f"Input {self.index + 1}")
        group_layout = QVBoxLayout()
        group.setLayout(group_layout)

        # Device Selector
        self.device_combo = QComboBox()
        self.populate_devices()
        self.device_combo.currentIndexChanged.connect(self.start_stream)
        group_layout.addWidget(QLabel("Select Input Device:"))
        group_layout.addWidget(self.device_combo)

        # Volume Slider
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(QLabel("Vol:"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.slider.valueChanged.connect(self.update_volume)
        vol_layout.addWidget(self.slider)
        group_layout.addLayout(vol_layout)

        # Remove Button
        btn_remove = QPushButton("Remove Input")
        btn_remove.setStyleSheet("background-color: #ffcccc;")
        btn_remove.clicked.connect(self.close_strip)
        group_layout.addWidget(btn_remove)

        layout.addWidget(group)

    def populate_devices(self):
        """Lists all available input devices."""
        self.device_combo.clear()
        self.device_combo.addItem("Select Device...", None)
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev['max_input_channels'] > 0:
                # We store the device ID (i) as user data
                self.device_combo.addItem(f"{dev['name']}", i)

    def update_volume(self):
        self.volume = self.slider.value() / 100.0

    def audio_callback(self, indata, frames, time, status):
        """Callback running in a separate thread by sounddevice."""
        if status:
            print(f"Input {self.index} status: {status}")
        
        # Put a copy of the audio data into the queue
        # If queue is full, we drop the oldest frame (latency management)
        if self.audio_queue.full():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass
        
        self.audio_queue.put(indata.copy())

    def start_stream(self):
        self.stop_stream()
        
        device_idx = self.device_combo.currentData()
        if device_idx is None:
            return

        try:
            self.stream = sd.InputStream(
                device=device_idx,
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                callback=self.audio_callback
            )
            self.stream.start()
            self.is_active = True
            print(f"Started Input Stream on device {device_idx}")
        except Exception as e:
            QMessageBox.critical(self, "Audio Error", f"Could not open input device:\n{str(e)}")
            self.device_combo.setCurrentIndex(0)

    def stop_stream(self):
        self.is_active = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        # Clear queue
        with self.audio_queue.mutex:
            self.audio_queue.queue.clear()

    def get_audio_chunk(self):
        """Called by the Master Output to get the current chunk of audio."""
        if not self.is_active or self.audio_queue.empty():
            return np.zeros((BLOCK_SIZE, CHANNELS), dtype='float32')
        
        try:
            data = self.audio_queue.get_nowait()
            return data * self.volume
        except queue.Empty:
            return np.zeros((BLOCK_SIZE, CHANNELS), dtype='float32')

    def close_strip(self):
        self.stop_stream()
        self.parent_mixer.remove_input_strip(self)
        self.deleteLater()


class AudioRouterApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PythonAudioRoute")
        self.resize(500, 600)

        self.input_strips = []
        self.output_stream = None
        
        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout()
        main_widget.setLayout(main_layout)

        # --- Output Section ---
        out_group = QGroupBox("Master Output")
        out_layout = QVBoxLayout()
        out_group.setLayout(out_layout)
        
        out_layout.addWidget(QLabel("Select Output Device (Headphones/Speakers):"))
        self.out_combo = QComboBox()
        self.populate_output_devices()
        self.out_combo.currentIndexChanged.connect(self.restart_output_stream)
        out_layout.addWidget(self.out_combo)
        
        main_layout.addWidget(out_group)

        # --- Inputs Section (Scrollable) ---
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.inputs_container = QWidget()
        self.inputs_layout = QVBoxLayout()
        self.inputs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.inputs_container.setLayout(self.inputs_layout)
        scroll.setWidget(self.inputs_container)
        
        main_layout.addWidget(QLabel("Input Sources:"))
        main_layout.addWidget(scroll)

        # --- Controls ---
        btn_add = QPushButton("+ Add Input Source")
        btn_add.setStyleSheet("background-color: #ccffcc; font-weight: bold; padding: 10px;")
        btn_add.clicked.connect(self.add_input_strip)
        main_layout.addWidget(btn_add)

    def populate_output_devices(self):
        self.out_combo.clear()
        self.out_combo.addItem("Select Output...", None)
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev['max_output_channels'] > 0:
                self.out_combo.addItem(f"{dev['name']}", i)

    def add_input_strip(self):
        index = len(self.input_strips)
        strip = AudioInputStrip(self, index)
        self.input_strips.append(strip)
        self.inputs_layout.addWidget(strip)

    def remove_input_strip(self, strip):
        if strip in self.input_strips:
            self.input_strips.remove(strip)

    def output_callback(self, outdata, frames, time, status):
        """
        The Master Mixer.
        It pulls data from all Input Strips, sums them up, and sends to speakers.
        """
        if status:
            print(f"Output status: {status}")

        # Start with silence
        mixed_audio = np.zeros((frames, CHANNELS), dtype='float32')
        
        # Add audio from every active input strip
        for strip in self.input_strips:
            chunk = strip.get_audio_chunk()
            # Ensure chunk size matches (handle edge cases)
            if len(chunk) == len(mixed_audio):
                mixed_audio += chunk

        # Clipping protection (prevent distortion if sum > 1.0)
        np.clip(mixed_audio, -1.0, 1.0, out=mixed_audio)
        
        outdata[:] = mixed_audio

    def restart_output_stream(self):
        if self.output_stream:
            self.output_stream.stop()
            self.output_stream.close()
            self.output_stream = None

        device_idx = self.out_combo.currentData()
        if device_idx is None:
            return

        try:
            self.output_stream = sd.OutputStream(
                device=device_idx,
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK_SIZE,
                callback=self.output_callback
            )
            self.output_stream.start()
            print(f"Started Output Stream on device {device_idx}")
        except Exception as e:
            QMessageBox.critical(self, "Output Error", f"Could not open output device:\n{str(e)}")
            self.out_combo.setCurrentIndex(0)

    def closeEvent(self, event):
        """Cleanup on app close"""
        if self.output_stream:
            self.output_stream.stop()
            self.output_stream.close()
        for strip in self.input_strips:
            strip.stop_stream()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = AudioRouterApp()
    window.show()
    sys.exit(app.exec())