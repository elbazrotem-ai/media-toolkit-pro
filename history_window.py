import os
import sqlite3
import shutil
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLineEdit, QPushButton,
                             QTextEdit, QLabel, QHBoxLayout, QFileDialog,
                             QRadioButton, QComboBox, QMessageBox, QGroupBox)
from PySide6.QtCore import Qt
from pygame import mixer

from database import DB_PATH # Import DB_PATH from database.py

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
