import os
import sys
import shutil
import yt_dlp
import tqdm as tqdm_module
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QLineEdit, QPushButton, QTextEdit, QLabel, QProgressBar,
                             QHBoxLayout, QFileDialog, QRadioButton,
                             QComboBox, QMessageBox, QGroupBox)
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFont
from pathlib import Path
from faster_whisper import WhisperModel
import sqlite3
from pygame import mixer
import urllib.request
import json
from config import APP_VERSION, GITHUB_REPO
from database import init_db, DB_PATH, INTERNAL_DIR
from history_window import HistoryWindow
from processing_thread import ProcessingThread
from utils import get_ffmpeg_path, resource_path
from update_checker import UpdateChecker

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.history_window = HistoryWindow()
        self.init_ui()
        self.toggle_settings_visibility(0)
        self.update_checker = UpdateChecker()
        self.update_checker.update_available.connect(self.on_update_available)
        self.update_checker.start()

    def toggle_settings_visibility(self, index):
        is_transcribe = (index == 1)  # 1 = "Transcribe to Text"

        # Audio quality — only for MP3
        self.quality_combo.setVisible(not is_transcribe)
        # find the QLabel for quality and hide it too
        self.quality_label.setVisible(not is_transcribe)

        # Whisper model — only for transcription
        self.model_combo.setVisible(is_transcribe)
        self.model_label.setVisible(is_transcribe)

    def init_ui(self):
        self.setWindowTitle("Media Toolkit Pro 🛠️")
        self.setGeometry(100, 100, 900, 750)
        self.setStyleSheet(self._stylesheet())

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title = QLabel("Media Toolkit Pro 🛠️")
        font  = QFont()
        font.setPointSize(18)
        font.setBold(True)
        title.setFont(font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_layout.addWidget(title)

        # ── Source ────────────────────────────────────────────────────────────
        source_group  = QGroupBox("Source Selection")
        source_layout = QHBoxLayout()
        self.radio_url   = QRadioButton("YouTube / Playlist URL")
        self.radio_local = QRadioButton("Local File")
        self.radio_url.setChecked(True)
        self.radio_url.toggled.connect(self.toggle_source_input)
        source_layout.addWidget(self.radio_url)
        source_layout.addWidget(self.radio_local)
        source_layout.addStretch()
        self.history_btn = QPushButton("History")
        self.history_btn.setMinimumHeight(35)
        self.history_btn.setMaximumWidth(120)
        self.history_btn.clicked.connect(self.show_history)
        source_layout.addWidget(self.history_btn)
        source_group.setLayout(source_layout)
        main_layout.addWidget(source_group)

        # File Naming Section
        naming_group = QGroupBox("File Naming") # Fixed spelling from 'nameing'
        naming_layout = QHBoxLayout()
        
        self.naming_input = QLineEdit()
        self.naming_input.setPlaceholderText("Enter custom filename (optional)...")
        self.naming_input.setMaximumHeight(35)
        
        naming_layout.addWidget(self.naming_input)
        naming_group.setLayout(naming_layout) # Call this only once
        
        main_layout.addWidget(naming_group)

        # ── Input ─────────────────────────────────────────────────────────────
        input_group  = QGroupBox("Input")
        input_layout = QHBoxLayout()
        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("Enter YouTube URL or paste link...")
        self.input_field.setMinimumHeight(35)
        input_layout.addWidget(self.input_field)
        self.browse_btn = QPushButton("📁 Browse Files")
        self.browse_btn.setVisible(False)
        self.browse_btn.setMinimumHeight(35)
        self.browse_btn.setMaximumWidth(160)
        self.browse_btn.clicked.connect(self.select_local_file)
        input_layout.addWidget(self.browse_btn)
        self.help_btn = QPushButton("❓ Help")
        self.help_btn.setMinimumHeight(35)
        self.help_btn.setMaximumWidth(100)
        self.help_btn.clicked.connect(self.show_help)
        input_layout.addWidget(self.help_btn)
        input_group.setLayout(input_layout)
        main_layout.addWidget(input_group)

        # ── Output ────────────────────────────────────────────────────────────
        output_group  = QGroupBox("Output Directory")
        out_layout    = QHBoxLayout()
        self.path_input = QLineEdit(
            os.path.join(str(Path.home()), "Downloads"))
        self.path_input.setMinimumHeight(35)
        out_layout.addWidget(self.path_input)
        self.output_browse_btn = QPushButton("📂 Browse")
        self.output_browse_btn.setMinimumHeight(35)
        self.output_browse_btn.setMaximumWidth(120)
        self.output_browse_btn.clicked.connect(self.select_output_folder)
        out_layout.addWidget(self.output_browse_btn)
        output_group.setLayout(out_layout)
        main_layout.addWidget(output_group)

        # ── Settings ──────────────────────────────────────────────────────────
        settings_group  = QGroupBox("Settings")
        settings_layout = QHBoxLayout()

        settings_layout.addWidget(QLabel("Action:"))
        self.action_combo = QComboBox()
        self.action_combo.addItems(["Download MP3 Only", "Transcribe to Text"])
        self.action_combo.setMinimumHeight(35)
        self.action_combo.setMinimumWidth(180)
        settings_layout.addWidget(self.action_combo)
        self.action_combo.currentIndexChanged.connect(self.toggle_settings_visibility)

        settings_layout.addSpacing(20)
        self.quality_label = QLabel("Audio Quality:")
        settings_layout.addWidget(self.quality_label)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["128 kbps", "192 kbps", "320 kbps"])
        self.quality_combo.setCurrentIndex(1)
        self.quality_combo.setMinimumHeight(35)
        self.quality_combo.setMinimumWidth(120)
        settings_layout.addWidget(self.quality_combo)

        settings_layout.addSpacing(20)
        self.model_label = QLabel("Whisper Model:")
        settings_layout.addWidget(self.model_label)
        self.model_combo = QComboBox()
        self.model_combo.addItems(
            ["tiny", "base", "small", "medium", "large-v2", "large-v3"])
        self.model_combo.setCurrentIndex(2)   # default: small
        self.model_combo.setMinimumHeight(35)
        self.model_combo.setMinimumWidth(120)
        self.model_combo.setToolTip(
            "tiny/base: fast, less accurate\n"
            "small: balanced (default)\n"
            "medium: better accuracy\n"
            "large-v2/v3: best accuracy, slowest"
        )
        settings_layout.addWidget(self.model_combo)
        settings_layout.addStretch()
        settings_group.setLayout(settings_layout)
        main_layout.addWidget(settings_group)

        # ── Progress ──────────────────────────────────────────────────────────
        progress_group  = QGroupBox("Progress")
        progress_layout = QVBoxLayout()
        self.progress_label = QLabel("Ready")
        self.progress_label.setStyleSheet("color: #00d4ff;")
        progress_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(25)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        progress_group.setLayout(progress_layout)
        main_layout.addWidget(progress_group)

        # ── Logs ──────────────────────────────────────────────────────────────
        logs_group  = QGroupBox("Logs")
        logs_layout = QVBoxLayout()
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setMinimumHeight(200)
        logs_layout.addWidget(self.log_display)
        logs_group.setLayout(logs_layout)
        main_layout.addWidget(logs_group)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.start_btn = QPushButton("🚀 Start Process")
        self.start_btn.setMinimumHeight(45)
        self.start_btn.setMinimumWidth(200)
        self.start_btn.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.start_btn.clicked.connect(self.start_process)
        btn_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⛔ Stop")
        self.stop_btn.setMinimumHeight(45)
        self.stop_btn.setMinimumWidth(120)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        self.stop_btn.setStyleSheet("background-color: #b91c1c;")
        self.stop_btn.clicked.connect(self.stop_process)
        btn_layout.addWidget(self.stop_btn)

        self.clear_btn = QPushButton("🗑️ Clear Logs")
        self.clear_btn.setMinimumHeight(45)
        self.clear_btn.setMinimumWidth(120)
        self.clear_btn.clicked.connect(self.log_display.clear)
        btn_layout.addWidget(self.clear_btn)

        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

    def filenameinput(self):
        
    def toggle_source_input(self):
        is_local = self.radio_local.isChecked()
        self.browse_btn.setVisible(is_local)
        self.input_field.setPlaceholderText(
            "Select a file..." if is_local
            else "Enter YouTube URL or paste link...")
        if is_local:
            self.input_field.clear()

    def select_local_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Media File", "",
            "Media (*.mp4 *.mp3 *.wav *.m4a *.flac)")
        if path:
            self.input_field.setText(path)

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Output Folder")
        if folder:
            self.path_input.setText(folder)

    def show_help(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Help / Instructions")
        msg.setText(
            "<h2>How to Use Media Toolkit Pro</h2>"

            "<p><b>1. Choose Source:</b></p>"
            "<ul>"
            "<li><b>YouTube / Playlist:</b> Paste a YouTube video or playlist URL.</li>"
            "<li><b>Local File:</b> Select an audio/video file from your computer.</li>"
            "</ul>"

            "<p><b>2. Select Output Location:</b> Where to save the downloaded files "
            "(default: Downloads). A separate internal copy is always kept for History.</p>"

            "<p><b>3. Choose Action:</b></p>"
            "<ul>"
            "<li><b>Download MP3 Only:</b> Extracts and saves audio in MP3 format.</li>"
            "<li><b>Transcribe to Text:</b> Downloads MP3 AND generates a transcript using AI.</li>"
            "</ul>"

            "<p><b>4. Set Audio Quality:</b></p>"
            "<ul>"
            "<li><b>128 kbps:</b> Lower quality, smaller file</li>"
            "<li><b>192 kbps:</b> Standard quality (recommended)</li>"
            "<li><b>320 kbps:</b> High quality, larger file</li>"
            "</ul>"

            "<p><b>5. Choose Whisper Model:</b></p>"
            "<p>Models are <b>downloaded automatically</b> on first use and cached "
            "locally — no manual setup needed.</p>"
            "<table border='1' cellpadding='6' cellspacing='0' "
            "style='border-collapse:collapse; width:100%;'>"
            "<tr style='background-color:#1f6feb; color:white;'>"
            "<th>Model</th><th>Speed</th><th>Accuracy</th><th>RAM</th></tr>"
            "<tr><td>tiny</td><td>⚡⚡⚡⚡</td><td>⭐</td><td>~1 GB</td></tr>"
            "<tr><td>base</td><td>⚡⚡⚡</td><td>⭐⭐</td><td>~1 GB</td></tr>"
            "<tr><td>small</td><td>⚡⚡</td><td>⭐⭐⭐</td><td>~2 GB</td></tr>"
            "<tr><td>medium</td><td>⚡</td><td>⭐⭐⭐⭐</td><td>~5 GB</td></tr>"
            "<tr><td>large-v2</td><td>🐢</td><td>⭐⭐⭐⭐⭐</td><td>~10 GB</td></tr>"
            "<tr><td>large-v3</td><td>🐢</td><td>⭐⭐⭐⭐⭐</td><td>~10 GB</td></tr>"
            "</table>"
            "<p><i>💡 Start with <b>small</b> for a good balance. "
            "Use <b>large-v3</b> for best accuracy on important content.</i></p>"

            "<p><b>6. Click '🚀 Start Process'</b> and monitor progress in the logs.</p>"
            "<p><b>7. Use '⛔ Stop'</b> to cancel at any time.</p>"

            "<p><b>8. History window:</b></p>"
            "<ul>"
            "<li>Every processed file is saved internally — it stays in History "
            "even if you delete the file from your Downloads.</li>"
            "<li><b>▶️ Play</b> — listen directly inside the app.</li>"
            "<li><b>⏹️ Stop</b> — stop playback.</li>"
            "<li><b>⬇️ Re-download</b> — copy the internal file to any folder.</li>"
            "<li><b>🗑️ Delete</b> — permanently removes the entry and its internal file.</li>"
            "</ul>"
        )
        msg.setStyleSheet("background-color: #161b22; color: #c9d1d9;")
        msg.exec()

        # ── Process control ───────────────────────────────────────────────────────

    def start_process(self):
        source = self.input_field.text().strip()
        if not source:
            QMessageBox.warning(self, "Input Error",
                                "Please enter a URL or select a file!")
            return

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_label.setText("Processing...")
        self.progress_bar.setValue(0)

        is_local   = self.radio_local.isChecked()
        action  = "text" if self.action_combo.currentIndex() == 1 else "mp3"
        quality = "320" if action == "text" else self.quality_combo.currentText().split()[0]
        model_size = self.model_combo.currentText()

        self.thread = ProcessingThread(
            source, self.path_input.text(),
            is_local, action, quality, model_size
        )
        self.thread.log_signal.connect(self.on_log_update)
        self.thread.progress_signal.connect(self.on_progress_update)
        self.thread.finished_signal.connect(self.on_process_finished)
        self.thread.start()

    def stop_process(self):
        if hasattr(self, 'thread') and self.thread.isRunning():
            self.thread.stop()
            self.stop_btn.setEnabled(False)
            self.progress_label.setText("⛔ Stopping...")

    def on_log_update(self, message):
        # Map emoji prefixes to colors
        if message.startswith("✅"):
            color = "#7ee787"   # green
        elif message.startswith("❌"):
            color = "#f85149"   # red
        elif message.startswith("⛔"):
            color = "#ff7b72"   # orange-red
        elif message.startswith("📥") or message.startswith("⬇️"):
            color = "#58a6ff"   # blue
        elif message.startswith("📝"):
            color = "#d2a8ff"   # purple
        elif message.startswith("🧠"):
            color = "#ffa657"   # orange
        elif message.startswith("💾") or message.startswith("📋"):
            color = "#8b949e"   # gray
        elif message.startswith("📂"):
            color = "#79c0ff"   # light blue
        elif message.startswith("⚠️"):
            color = "#e3b341"   # yellow
        else:
            color = "#c9d1d9"   # default

        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        html = (
            f'<span style="color:#484f58;">[{timestamp}]</span> '
            f'<span style="color:{color};">{message}</span>'
        )
        self.log_display.append(html)
        self.log_display.verticalScrollBar().setValue(
            self.log_display.verticalScrollBar().maximum()
        )

        if "Loading Whisper" in message:
            self.progress_label.setText("⬇️ Downloading / loading model...")
        elif "Transcribing" in message:
            self.progress_label.setText("📝 Transcribing...")
        elif "Model ready" in message:
            self.progress_label.setText("✅ Model loaded!")
            self.progress_bar.setValue(0)

    def on_progress_update(self, value):
        self.progress_bar.setValue(int(value))
        self.progress_label.setText(f"Progress: {int(value)}%")

    def on_process_finished(self):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_label.setText("✅ Process completed!")
        self.history_window.load_history()
        QMessageBox.information(self, "Success",
                                "Process completed successfully!")

    def show_history(self):
        self.history_window.load_history()
        self.history_window.show()

    def _stylesheet(self):
        return """
        QMainWindow  { background-color: #0d1117; }
        QWidget      { background-color: #0d1117; color: #c9d1d9; }
        QGroupBox    { color: #c9d1d9; border: 2px solid #30363d; border-radius: 6px;
                       margin-top: 10px; padding-top: 10px; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; }
        QLineEdit    { background-color: #161b22; border: 2px solid #30363d;
                       border-radius: 6px; padding: 8px; color: #c9d1d9;
                       selection-background-color: #1f6feb; }
        QLineEdit:focus { border: 2px solid #58a6ff; }
        QPushButton  { background-color: #238636; color: white; border: none;
                       border-radius: 6px; padding: 8px 16px; font-weight: bold; margin: 2px; }
        QPushButton:hover    { background-color: #2ea043; }
        QPushButton:pressed  { background-color: #1a7f37; }
        QPushButton:disabled { background-color: #30363d; color: #6e7681; }
        QTextEdit    { background-color: #0d1117; border: 2px solid #30363d;
                       border-radius: 6px; color: #7ee787;
                       font-family: 'Courier New', monospace; font-size: 10pt; padding: 8px; }
        QComboBox    { background-color: #161b22; border: 2px solid #30363d;
                       border-radius: 6px; padding: 8px; color: #c9d1d9;
                       selection-background-color: #1f6feb; }
        QComboBox:hover { border: 2px solid #58a6ff; }
        QComboBox QAbstractItemView { background-color: #161b22; color: #c9d1d9;
                       selection-background-color: #1f6feb; border: 1px solid #30363d; }
        QProgressBar { background-color: #161b22; border: 2px solid #30363d;
                       border-radius: 6px; text-align: center; color: #c9d1d9; }
        QProgressBar::chunk { background-color: #58a6ff; border-radius: 4px; }
        QLabel       { color: #c9d1d9; }
        QRadioButton { color: #c9d1d9; spacing: 5px; }
        QRadioButton::indicator { width: 18px; height: 18px; }
        QRadioButton::indicator:unchecked { background-color: #161b22;
                       border: 2px solid #30363d; border-radius: 9px; }
        QRadioButton::indicator:checked  { background-color: #1f6feb;
                       border: 2px solid #1f6feb; border-radius: 9px; }
        """

    def on_update_available(self, new_version):
        msg = QMessageBox(self)
        msg.setWindowTitle("Update Available 🚀")
        msg.setText(
            f"<h3>A new version is available!</h3>"
            f"<p>Current version: <b>v{APP_VERSION}</b></p>"
            f"<p>New version: <b>v{new_version}</b></p>"
            f"<p>Would you like to download the update?</p>"
        )
        msg.setStyleSheet("background-color: #161b22; color: #c9d1d9;")
        update_btn = msg.addButton("⬇️ Download Update", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() == update_btn:
            import webbrowser
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

if __name__ == "__main__":
    init_db()
    app    = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())