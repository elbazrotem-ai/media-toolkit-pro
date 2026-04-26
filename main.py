APP_VERSION = "1.0.0" 
GITHUB_REPO = "elbazrotem-ai/media-toolkit-pro"

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

DB_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history.db")
INTERNAL_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "internal_storage")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            filename         TEXT,
            audio_path       TEXT,
            transcript_path  TEXT,
            timestamp        DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_ffmpeg_path():
    """Return the directory containing ffmpeg/ffprobe, or None if on PATH."""
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS

    script_dir  = os.path.dirname(os.path.abspath(__file__))
    ffmpeg_exe  = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    if os.path.exists(os.path.join(script_dir, ffmpeg_exe)):
        return script_dir

    if shutil.which("ffmpeg"):
        return None          # yt-dlp will find it on PATH

    return script_dir        # fallback — let yt-dlp complain if missing

class SignalTqdm(tqdm_module.tqdm):
    _signal = None

    def update(self, n=1):
        super().update(n)
        if self._signal and self.total and self.total > 0:
            self._signal.emit((self.n / self.total) * 100)

class HistoryWindow(QWidget):
    def __init__(self):
        super().__init__()
        mixer.init()
        self.setWindowTitle("History & Player 📜")
        self.setGeometry(150, 150, 650, 560)
        self.entries        = []
        self.current_audio  = ""
        self.current_id     = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # ── Selector ──────────────────────────────────────────────────────────
        layout.addWidget(QLabel("Select from History:"))
        self.history_list = QComboBox()
        self.history_list.currentIndexChanged.connect(self.load_entry)
        layout.addWidget(self.history_list)

        # ── Transcript viewer ─────────────────────────────────────────────────
        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        layout.addWidget(self.text_view)

        # ── Player buttons ────────────────────────────────────────────────────
        player_layout = QHBoxLayout()

        self.play_btn = QPushButton("▶️ Play Audio")
        self.play_btn.clicked.connect(self.play_audio)
        player_layout.addWidget(self.play_btn)

        self.stop_audio_btn = QPushButton("⏹️ Stop Audio")
        self.stop_audio_btn.clicked.connect(self.stop_audio)
        player_layout.addWidget(self.stop_audio_btn)

        layout.addLayout(player_layout)

        # ── Action buttons ────────────────────────────────────────────────────
        action_layout = QHBoxLayout()

        self.download_btn = QPushButton("⬇️ Re-download to Folder")
        self.download_btn.clicked.connect(self.redownload_audio)
        action_layout.addWidget(self.download_btn)

        self.delete_btn = QPushButton("🗑️ Delete from History")
        self.delete_btn.setStyleSheet("background-color: #b91c1c;")
        self.delete_btn.clicked.connect(self.delete_entry)
        action_layout.addWidget(self.delete_btn)

        layout.addLayout(action_layout)

        self.setStyleSheet(self._stylesheet())
        self.load_history()

    def load_history(self):
        self.history_list.clear()
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, filename, audio_path, transcript_path "
            "FROM history ORDER BY id DESC"
        )
        self.entries = cursor.fetchall()
        conn.close()
        for entry in self.entries:
            self.history_list.addItem(entry[1])

    def load_entry(self, index):
        if 0 <= index < len(self.entries):
            entry_id, _, audio, transcript = self.entries[index]
            self.current_audio = audio
            self.current_id    = entry_id
            if transcript and os.path.exists(transcript):
                with open(transcript, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                html_lines = []
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    # Color the [MM:SS] timestamp separately
                    if line.startswith("[") and "]" in line:
                        ts_end = line.index("]") + 1
                        ts   = line[:ts_end]
                        text = line[ts_end:]
                        html_lines.append(
                            f'<span style="color:#58a6ff; font-weight:bold;">{ts}</span>'
                            f'<span style="color:#c9d1d9;">{text}</span>'
                        )
                    else:
                        html_lines.append(
                            f'<span style="color:#c9d1d9;">{line}</span>'
                        )
                self.text_view.setHtml("<br>".join(html_lines))
            else:
                self.text_view.setText("(No transcript available)")

    def play_audio(self):
        if self.current_audio and os.path.exists(self.current_audio):
            try:
                mixer.music.stop()
                mixer.music.load(self.current_audio)
                mixer.music.play()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not play audio:\n{e}")
        else:
            QMessageBox.warning(self, "File Not Found",
                                "The internal audio file is missing.")

    def stop_audio(self):
        mixer.music.stop()

    def redownload_audio(self):
        if not self.current_audio or not os.path.exists(self.current_audio):
            QMessageBox.warning(self, "Not Found",
                                "Internal audio file not found.")
            return
        dest_folder = QFileDialog.getExistingDirectory(
            self, "Select Destination Folder")
        if dest_folder:
            dest = os.path.join(dest_folder,
                                os.path.basename(self.current_audio))
            shutil.copy2(self.current_audio, dest)
            QMessageBox.information(self, "Done",
                                    f"File saved to:\n{dest}")

    def delete_entry(self):
        if self.current_id is None:
            return
        reply = QMessageBox.question(
            self, "Confirm Delete",
            "Delete this entry and its internally stored audio file?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Remove internal audio
        if self.current_audio and os.path.exists(self.current_audio):
            try:
                os.remove(self.current_audio)
            except Exception:
                pass

        # Remove internal transcript
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transcript_path FROM history WHERE id=?",
            (self.current_id,)
        )
        row = cursor.fetchone()
        if row and row[0] and os.path.exists(row[0]):
            try:
                os.remove(row[0])
            except Exception:
                pass

        cursor.execute("DELETE FROM history WHERE id=?", (self.current_id,))
        conn.commit()
        conn.close()

        self.current_audio = ""
        self.current_id    = None
        self.text_view.clear()
        self.load_history()

    def _stylesheet(self):
        return """
        QWidget          { background-color: #0d1117; color: #c9d1d9; }
        QComboBox        { background-color: #161b22; border: 2px solid #30363d;
                           border-radius: 6px; padding: 8px; color: #c9d1d9; }
        QComboBox:hover  { border: 2px solid #58a6ff; }
        QComboBox QAbstractItemView { background-color: #161b22; color: #c9d1d9;
                           selection-background-color: #1f6feb; }
        QTextEdit { 
            background-color: #0d1117; 
            border: 2px solid #30363d;
            border-radius: 6px; 
            color: #c9d1d9;              /* white-ish instead of green */
            font-family: 'Segoe UI', Arial, sans-serif;  /* readable font */
            font-size: 11pt; 
            line-height: 1.6;
            padding: 10px; 
        }
        QPushButton      { background-color: #238636; color: white; border: none;
                           border-radius: 6px; padding: 8px 16px; font-weight: bold; }
        QPushButton:hover { background-color: #2ea043; }
        QLabel           { color: #c9d1d9; }
        """

class ProcessingThread(QThread):
    log_signal      = Signal(str)
    progress_signal = Signal(float)
    finished_signal = Signal()

    def __init__(self, source, output_folder, is_local, action, quality, model_size):
        super().__init__()
        self.source        = source
        self.output_folder = output_folder
        self.is_local      = is_local
        self.action        = action
        self.quality       = quality
        self.model_size    = model_size
        self._stop_flag    = False

    def stop(self):
        self._stop_flag = True

    def progress_hook(self, d):
        if d['status'] == 'downloading':
            p = d.get('_percent_str', '0%').replace('%', '').strip()
            try:
                self.progress_signal.emit(float(p))
            except Exception:
                pass

    def run(self):
        try:
            init_db()
            os.makedirs(self.output_folder, exist_ok=True)
            os.makedirs(INTERNAL_DIR,        exist_ok=True)

            files_to_process = []
            ffmpeg_bin       = get_ffmpeg_path()

            # ── 1. Download or use local ───────────────────────────────────
            if not self.is_local:
                self.log_signal.emit("📥 Starting YouTube download process...")
                ydl_opts = {
                    'format':        'bestaudio/best',
                    'outtmpl':       os.path.join(self.output_folder,
                                                  '%(title)s.%(ext)s'),
                    'progress_hooks': [self.progress_hook],
                    'ignoreerrors':  True,
                    'postprocessors': [{
                        'key':              'FFmpegExtractAudio',
                        'preferredcodec':   'mp3',
                        'preferredquality': self.quality,
                    }],
                }
                if ffmpeg_bin:
                    ydl_opts['ffmpeg_location'] = ffmpeg_bin

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(self.source, download=True)
                    if 'entries' in info:           # playlist
                        for entry in info['entries']:
                            if entry:
                                f_path = (ydl.prepare_filename(entry)
                                          .rsplit('.', 1)[0] + '.mp3')
                                files_to_process.append(f_path)
                    else:                           # single video
                        f_path = (ydl.prepare_filename(info)
                                  .rsplit('.', 1)[0] + '.mp3')
                        files_to_process.append(f_path)
            else:
                self.log_signal.emit(
                    f"📂 Using local file: {os.path.basename(self.source)}")
                files_to_process.append(self.source)

            # ── 2. Load Whisper model (with download-progress patching) ────
            conn   = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            model  = None

            if self.action == 'text':
                self.log_signal.emit(
                    f"🧠 Loading Whisper model: {self.model_size} "
                    f"(downloading if not cached)...")
                self.progress_signal.emit(0)

                original_tqdm        = tqdm_module.tqdm
                SignalTqdm._signal   = self.progress_signal
                tqdm_module.tqdm     = SignalTqdm
                try:
                    model = WhisperModel(self.model_size,
                                        device="cpu",
                                        compute_type="int8")
                    self.log_signal.emit("✅ Model ready.")
                finally:
                    tqdm_module.tqdm   = original_tqdm
                    SignalTqdm._signal = None
                    self.progress_signal.emit(0)   # reset bar

            # ── 3. Process each file ───────────────────────────────────────
            for audio_file in files_to_process:
                if self._stop_flag:
                    self.log_signal.emit("⛔ Process stopped by user.")
                    break

                if not os.path.exists(audio_file):
                    self.log_signal.emit(
                        f"⚠️ File not found, skipping: {audio_file}")
                    continue

                # Copy to internal storage so history survives deletion
                internal_audio = os.path.join(INTERNAL_DIR,
                                              os.path.basename(audio_file))
                if os.path.abspath(audio_file) != os.path.abspath(internal_audio):
                    shutil.copy2(audio_file, internal_audio)
                    self.log_signal.emit(
                        f"💾 Saved internally: {os.path.basename(internal_audio)}")

                t_path = ""

                if self.action == 'text' and model:
                    self.log_signal.emit(
                        f"📝 Transcribing: {os.path.basename(audio_file)}")
                    segments, info = model.transcribe(audio_file, beam_size=5)

                    transcript_text = []
                    for segment in segments:
                        if self._stop_flag:
                            self.log_signal.emit(
                                "⛔ Transcription stopped by user.")
                            break
                        ts = (f"[{int(segment.start // 60):02d}:"
                              f"{int(segment.start % 60):02d}]")
                        transcript_text.append(f"{ts} {segment.text}")
                        self.progress_signal.emit(
                            (segment.end / info.duration) * 100)

                    if transcript_text:
                        t_path = os.path.join(
                            INTERNAL_DIR,
                            os.path.basename(audio_file).rsplit('.', 1)[0]
                            + "_transcript.txt"
                        )
                        with open(t_path, "w", encoding="utf-8") as f:
                            f.write("\n".join(transcript_text))
                        self.log_signal.emit(
                            f"✅ Transcript saved: {os.path.basename(t_path)}")

                cursor.execute(
                    "INSERT INTO history "
                    "(filename, audio_path, transcript_path) "
                    "VALUES (?, ?, ?)",
                    (os.path.basename(audio_file), internal_audio, t_path)
                )
                self.log_signal.emit(
                    f"📋 Added to history: {os.path.basename(audio_file)}")

            conn.commit()
            conn.close()

            if self.action != 'text':
                self.log_signal.emit("✅ Audio extraction completed.")

        except Exception as e:
            self.log_signal.emit(f"❌ Error: {e}")
        finally:
            self.finished_signal.emit()

class UpdateChecker(QThread):
    update_available = Signal(str)  # emits the new version string

    def run(self):
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "MediaToolkitPro"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read())
                latest = data.get("tag_name", "").lstrip("v")
                if latest and latest != APP_VERSION:
                    self.update_available.emit(latest)
        except Exception:
            pass  # no internet or no releases — stay silent

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

def resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

if __name__ == "__main__":
    init_db()
    app    = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())