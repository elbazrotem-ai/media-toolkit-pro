import os
import re
import sys
import json
import ctypes
import shutil
import sqlite3
import urllib.request
import webbrowser
import subprocess
from datetime import datetime
from pathlib import Path

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QPushButton, QTextEdit, QFileDialog, QComboBox,
        QProgressBar, QMessageBox, QGroupBox, QDialog, QLineEdit,
        QStatusBar, QSizePolicy, QListWidget, QListWidgetItem,
    )
    from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
    from PySide6.QtGui import QFont, QDragEnterEvent, QDropEvent
except ImportError as _e:
    print(f"[FATAL] PySide6 not found: {_e}\n  pip install PySide6")
    sys.exit(1)

try:
    from pygame import mixer as _pg_mixer
    _pg_mixer.init()
    HAVE_MIXER = True
except Exception:
    HAVE_MIXER = False

try:
    from faster_whisper import WhisperModel
    HAVE_WHISPER = True
except ImportError:
    HAVE_WHISPER = False

try:
    import tqdm as tqdm_module
    HAVE_TQDM = True
except ImportError:
    HAVE_TQDM = False

APP_NAME    = "MediaToolkit Pro"
APP_VERSION = "1.0.0"
GITHUB_REPO = "your-username/your-repo"   # ← update if you use the updater


# ── App directories ────────────────────────────────────────────────────────────

def _app_data_dir() -> str:
    app_slug = APP_NAME.replace(" ", "")
    if sys.platform == "win32":
        base = os.getenv("APPDATA", str(Path.home()))
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = str(Path.home() / ".local" / "share")
    path = os.path.join(base, app_slug)
    os.makedirs(path, exist_ok=True)
    return path


APP_DATA_DIR = _app_data_dir()
DB_PATH      = os.path.join(APP_DATA_DIR, "history.db")
INTERNAL_DIR = os.path.join(APP_DATA_DIR, "internal_storage")
os.makedirs(INTERNAL_DIR, exist_ok=True)


# ── Database ───────────────────────────────────────────────────────────────────

def _init_db() -> None:
    conn   = sqlite3.connect(DB_PATH)
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


# ── Sleep prevention ───────────────────────────────────────────────────────────

_caffeinate_proc = None   # macOS only


def _prevent_sleep() -> None:
    """Block OS sleep while processing."""
    global _caffeinate_proc
    if sys.platform == "win32":
        ES_CONTINUOUS      = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
    elif sys.platform == "darwin":
        try:
            _caffeinate_proc = subprocess.Popen(["caffeinate", "-i"])
        except FileNotFoundError:
            pass
    # Linux: best-effort via systemd-inhibit (fire-and-forget)
    elif sys.platform.startswith("linux"):
        try:
            subprocess.Popen([
                "systemd-inhibit",
                "--what=sleep:idle",
                "--who", APP_NAME,
                "--why", "Processing media",
                "--mode", "block",
                "sleep", "86400",          # holds for up to 24 h
            ])
        except FileNotFoundError:
            pass


def _allow_sleep() -> None:
    """Restore normal OS sleep behaviour."""
    global _caffeinate_proc
    if sys.platform == "win32":
        ES_CONTINUOUS = 0x80000000
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    elif sys.platform == "darwin" and _caffeinate_proc is not None:
        _caffeinate_proc.terminate()
        _caffeinate_proc = None


# ── tqdm shim ──────────────────────────────────────────────────────────────────

if HAVE_TQDM:
    class SignalTqdm(tqdm_module.tqdm):
        _signal = None

        def update(self, n: int = 1) -> None:
            super().update(n)
            if self._signal and self.total and self.total > 0:
                self._signal.emit((self.n / self.total) * 100)
else:
    class SignalTqdm:            # type: ignore[no-redef]
        _signal = None


# ══════════════════════════════════════════════════════════════════════════════
#  Processing thread
# ══════════════════════════════════════════════════════════════════════════════

class ProcessingThread(QThread):
    progress_signal = Signal(float)
    status_signal   = Signal(str)   # short status text for the label
    finished_signal = Signal()

    def __init__(
        self,
        source: str,
        output_folder: str,
        action: str,       # "audio" | "text"
        model_size: str,
        custom_name: str,
    ) -> None:
        super().__init__()
        self.source        = source
        self.output_folder = output_folder
        self.action        = action
        self.model_size    = model_size
        self.custom_name   = custom_name.strip()
        self._stop_flag    = False

    def stop(self) -> None:
        self._stop_flag = True

    @staticmethod
    def _unique_path(folder: str, name: str, ext: str) -> str:
        candidate = os.path.join(folder, name + ext)
        counter   = 1
        while os.path.exists(candidate):
            candidate = os.path.join(folder, f"{name}_{counter}{ext}")
            counter  += 1
        return candidate

    @staticmethod
    def _sanitize(name: str, fallback: str) -> str:
        clean = re.sub(r'[\\/:*?"<>|]', "", name).strip()
        return clean if clean else fallback

    def run(self) -> None:
        try:
            _init_db()
            os.makedirs(self.output_folder, exist_ok=True)
            os.makedirs(INTERNAL_DIR, exist_ok=True)

            src_name          = os.path.basename(self.source)
            src_stem, src_ext = os.path.splitext(src_name)
            out_stem          = self._sanitize(self.custom_name, src_stem)

            dest_path     = self._unique_path(self.output_folder, out_stem, src_ext)
            internal_path = self._unique_path(INTERNAL_DIR,       out_stem, src_ext)

            self.status_signal.emit(f"Copying  {src_name}…")
            shutil.copy2(self.source, dest_path)

            if os.path.abspath(dest_path) != os.path.abspath(internal_path):
                shutil.copy2(dest_path, internal_path)

            transcript_path = ""

            if self.action == "text":
                self.progress_signal.emit(0)
                self.status_signal.emit("⬇  Loading / downloading Whisper model…")

                if HAVE_TQDM:
                    original_tqdm      = tqdm_module.tqdm
                    SignalTqdm._signal = self.progress_signal
                    tqdm_module.tqdm   = SignalTqdm
                try:
                    model = WhisperModel(
                        self.model_size, device="cpu", compute_type="int8"
                    )
                finally:
                    if HAVE_TQDM:
                        tqdm_module.tqdm   = original_tqdm
                        SignalTqdm._signal = None
                    self.progress_signal.emit(0)

                self.status_signal.emit("Transcribing…")
                segments, seg_info = model.transcribe(dest_path, beam_size=5)
                lines: list[str]   = []

                for seg in segments:
                    if self._stop_flag:
                        self.status_signal.emit("⛔  Stopped by user")
                        return
                    mm  = int(seg.start // 60)
                    ss  = int(seg.start %  60)
                    lines.append(f"[{mm:02d}:{ss:02d}] {seg.text.strip()}")
                    if seg_info.duration > 0:
                        self.progress_signal.emit(
                            min((seg.end / seg_info.duration) * 100, 100)
                        )

                if lines:
                    transcript_path = self._unique_path(
                        INTERNAL_DIR, out_stem + "_transcript", ".txt"
                    )
                    with open(transcript_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(lines))
                else:
                    self.status_signal.emit("⚠️  No speech detected")

            conn   = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO history (filename, audio_path, transcript_path) "
                "VALUES (?, ?, ?)",
                (os.path.basename(dest_path), internal_path, transcript_path),
            )
            conn.commit()
            conn.close()

            self.finished_signal.emit()

        except Exception as exc:
            self.status_signal.emit(f"❌  Error: {exc}")
            self.finished_signal.emit()


# ══════════════════════════════════════════════════════════════════════════════
#  Update checker
# ══════════════════════════════════════════════════════════════════════════════

class UpdateChecker(QThread):
    update_available = Signal(str)

    def run(self) -> None:
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                headers={"User-Agent": APP_NAME},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                latest = json.loads(resp.read()).get("tag_name", "").lstrip("v")
                if latest and latest != APP_VERSION:
                    self.update_available.emit(latest)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
#  History window
# ══════════════════════════════════════════════════════════════════════════════

class HistoryWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} — History & Player")
        self.setGeometry(170, 170, 700, 600)
        self.setStyleSheet(_DARK_CSS)

        self.entries:       list   = []
        self.current_audio: str    = ""
        self.current_id:    object = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        layout.addWidget(QLabel("Select from history:"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._load_entry)
        layout.addWidget(self.combo)

        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setFont(QFont("Segoe UI", 10))
        layout.addWidget(self.text_view)

        player_row = QHBoxLayout()
        self.play_btn = QPushButton("Play Audio")
        self.play_btn.clicked.connect(self._play)
        self.stop_audio_btn = QPushButton("Stop")
        self.stop_audio_btn.clicked.connect(self._stop_audio)
        player_row.addWidget(self.play_btn)
        player_row.addWidget(self.stop_audio_btn)
        layout.addLayout(player_row)

        action_row = QHBoxLayout()
        self.export_audio_btn = QPushButton("Export Audio")
        self.export_audio_btn.clicked.connect(self._export_audio)
        self.export_txt_btn = QPushButton("Export Transcript")
        self.export_txt_btn.clicked.connect(self._export_transcript)
        self.del_btn = QPushButton("Delete Entry")
        self.del_btn.setStyleSheet("background-color: #b91c1c;")
        self.del_btn.clicked.connect(self._delete_entry)
        action_row.addWidget(self.export_audio_btn)
        action_row.addWidget(self.export_txt_btn)
        action_row.addWidget(self.del_btn)
        layout.addLayout(action_row)

        self.load_history()

    def load_history(self) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, filename, audio_path, transcript_path "
            "FROM history ORDER BY id DESC"
        )
        self.entries = cursor.fetchall()
        conn.close()
        for entry in self.entries:
            self.combo.addItem(entry[1])
        self.combo.blockSignals(False)
        if self.entries:
            self._load_entry(0)

    def _load_entry(self, index: int) -> None:
        if not (0 <= index < len(self.entries)):
            return
        entry_id, _, audio, transcript = self.entries[index]
        self.current_audio = audio or ""
        self.current_id    = entry_id

        if transcript and os.path.exists(transcript):
            with open(transcript, "r", encoding="utf-8") as f:
                lines = f.readlines()
            html_parts: list[str] = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[") and "]" in line:
                    end  = line.index("]") + 1
                    ts   = line[:end]
                    body = line[end:]
                    html_parts.append(
                        f'<span style="color:#58a6ff;font-weight:bold;">{ts}</span>'
                        f'<span style="color:#c9d1d9;">{body}</span>'
                    )
                else:
                    html_parts.append(f'<span style="color:#c9d1d9;">{line}</span>')
            self.text_view.setHtml("<br>".join(html_parts))
        else:
            self.text_view.setText("(No transcript for this entry)")

    def _play(self) -> None:
        if not HAVE_MIXER:
            QMessageBox.warning(self, "Unavailable",
                                "pygame is not installed — cannot play audio.")
            return
        if not self.current_audio or not os.path.exists(self.current_audio):
            QMessageBox.warning(self, "File Missing", "Internal audio file not found.")
            return
        try:
            _pg_mixer.music.stop()
            _pg_mixer.music.load(self.current_audio)
            _pg_mixer.music.play()
        except Exception as exc:
            QMessageBox.critical(self, "Playback Error", str(exc))

    def _stop_audio(self) -> None:
        if HAVE_MIXER:
            _pg_mixer.music.stop()

    def _export_audio(self) -> None:
        if not self.current_audio or not os.path.exists(self.current_audio):
            QMessageBox.warning(self, "Missing", "Audio file not found.")
            return
        dest_dir = QFileDialog.getExistingDirectory(self, "Export Audio To…")
        if dest_dir:
            dest = os.path.join(dest_dir, os.path.basename(self.current_audio))
            shutil.copy2(self.current_audio, dest)
            QMessageBox.information(self, "Exported", f"Saved to:\n{dest}")

    def _export_transcript(self) -> None:
        if self.current_id is None:
            return
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT transcript_path FROM history WHERE id=?",
            (self.current_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row or not row[0] or not os.path.exists(row[0]):
            QMessageBox.warning(self, "Missing", "No transcript found for this entry.")
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export Transcript As…",
            os.path.basename(row[0]),
            "Text files (*.txt)",
        )
        if dest:
            shutil.copy2(row[0], dest)
            QMessageBox.information(self, "Exported", f"Saved to:\n{dest}")

    def _delete_entry(self) -> None:
        if self.current_id is None:
            return
        reply = QMessageBox.question(
            self, "Confirm Delete",
            "Permanently delete this entry and its internal files?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT audio_path, transcript_path FROM history WHERE id=?",
            (self.current_id,),
        )
        row = cursor.fetchone()
        if row:
            for path in row:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass

        cursor.execute("DELETE FROM history WHERE id=?", (self.current_id,))
        conn.commit()
        conn.close()

        self.current_audio = ""
        self.current_id    = None
        self.text_view.clear()
        self.load_history()


# ══════════════════════════════════════════════════════════════════════════════
#  Drag-and-drop line edit
# ══════════════════════════════════════════════════════════════════════════════

class DropLineEdit(QLineEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isfile(path):
                self.setText(path)


# ══════════════════════════════════════════════════════════════════════════════
#  Main window
# ══════════════════════════════════════════════════════════════════════════════

MEDIA_FILTER = (
    "Audio & Video (*.mp3 *.wav *.m4a *.flac *.ogg "
    "*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)"
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  v{APP_VERSION}")
        self.setMinimumSize(820, 600)
        self.setStyleSheet(_DARK_CSS)

        self._worker:         ProcessingThread | None = None
        self._history_win:    HistoryWindow    | None = None
        self._update_checker: UpdateChecker           = UpdateChecker()

        # Queue: list of (source_path, custom_name) tuples
        self._queue: list[tuple[str, str]] = []

        self._build_ui()

        self._update_checker.update_available.connect(self._on_update)
        self._update_checker.start()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(14)
        root.setContentsMargins(20, 18, 20, 18)

        title = QLabel(APP_NAME)
        title.setFont(QFont("Segoe UI", 18, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        root.addWidget(title)

        # ── Source ────────────────────────────────────────────────────────────
        src_group  = QGroupBox("Source File  (drag & drop supported)")
        src_layout = QHBoxLayout(src_group)
        self.source_edit = DropLineEdit()
        self.source_edit.setPlaceholderText("Drag a file here, or click Browse…")
        self.source_edit.setReadOnly(True)
        self.source_edit.setMinimumHeight(34)
        src_layout.addWidget(self.source_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(110)
        browse_btn.clicked.connect(self._browse_source)
        src_layout.addWidget(browse_btn)
        root.addWidget(src_group)

        # ── Output ────────────────────────────────────────────────────────────
        out_group  = QGroupBox("Output")
        out_layout = QVBoxLayout(out_group)

        folder_row = QHBoxLayout()
        default_out = str(Path.home() / "Downloads")
        self.folder_edit = QLineEdit(default_out)
        self.folder_edit.setMinimumHeight(34)
        folder_row.addWidget(self.folder_edit)
        folder_btn = QPushButton("Browse…")
        folder_btn.setFixedWidth(110)
        folder_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(folder_btn)
        out_layout.addLayout(folder_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Custom filename (optional):"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Leave blank to keep original name")
        self.name_edit.setMinimumHeight(34)
        name_row.addWidget(self.name_edit)
        out_layout.addLayout(name_row)

        root.addWidget(out_group)

        # ── Settings ──────────────────────────────────────────────────────────
        settings_group  = QGroupBox("Settings")
        settings_layout = QHBoxLayout(settings_group)

        settings_layout.addWidget(QLabel("Action:"))
        self.action_combo = QComboBox()
        self.action_combo.addItems(["Copy audio only", "Transcribe to text"])
        self.action_combo.setMinimumHeight(34)
        self.action_combo.setMinimumWidth(180)
        self.action_combo.currentIndexChanged.connect(self._on_action_changed)
        settings_layout.addWidget(self.action_combo)

        settings_layout.addSpacing(20)
        self.model_label = QLabel("Whisper model:")
        settings_layout.addWidget(self.model_label)
        self.model_combo = QComboBox()
        self.model_combo.addItems(
            ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
        )
        self.model_combo.setCurrentText("small")
        self.model_combo.setMinimumHeight(34)
        self.model_combo.setMinimumWidth(120)
        self.model_combo.setToolTip(
            "tiny  — fastest, lowest accuracy\n"
            "base  — fast, better\n"
            "small — balanced  (recommended)\n"
            "medium / large-v3 — best accuracy, slower"
        )
        settings_layout.addWidget(self.model_combo)
        settings_layout.addStretch()
        root.addWidget(settings_group)

        self._on_action_changed(0)

        # ── Progress ──────────────────────────────────────────────────────────
        progress_group  = QGroupBox("Progress")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_label = QLabel("Ready")
        self.progress_label.setStyleSheet("color: #58a6ff;")
        progress_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumHeight(22)
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        root.addWidget(progress_group)

        # ── Queue panel ───────────────────────────────────────────────────────
        queue_group  = QGroupBox("Queue  (files waiting to be processed)")
        queue_layout = QVBoxLayout(queue_group)

        self.queue_list = QListWidget()
        self.queue_list.setMaximumHeight(100)
        self.queue_list.setToolTip("Files will be processed automatically one after another")
        queue_layout.addWidget(self.queue_list)

        queue_btn_row = QHBoxLayout()
        self.add_queue_btn = QPushButton("➕  Add File to Queue")
        self.add_queue_btn.setToolTip(
            "Add another audio/video file to process after the current one finishes"
        )
        self.add_queue_btn.clicked.connect(self._add_to_queue)
        queue_btn_row.addWidget(self.add_queue_btn)

        self.clear_queue_btn = QPushButton("Clear Queue")
        self.clear_queue_btn.setStyleSheet("background-color: #6e7681; color: white;")
        self.clear_queue_btn.clicked.connect(self._clear_queue)
        queue_btn_row.addWidget(self.clear_queue_btn)
        queue_btn_row.addStretch()

        self.queue_label = QLabel("Queue: 0 files")
        self.queue_label.setStyleSheet("color: #8b949e;")
        queue_btn_row.addWidget(self.queue_label)
        queue_layout.addLayout(queue_btn_row)

        root.addWidget(queue_group)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.start_btn = QPushButton("▶  Start")
        self.start_btn.setMinimumSize(180, 44)
        self.start_btn.setFont(QFont("Segoe UI", 11, QFont.Bold))
        self.start_btn.clicked.connect(self._start)
        btn_row.addWidget(self.start_btn)

        self.stop_btn = QPushButton("⏹  Stop")
        self.stop_btn.setMinimumSize(110, 44)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("background-color: #b91c1c;")
        self.stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self.stop_btn)

        history_btn = QPushButton("History")
        history_btn.setMinimumSize(110, 44)
        history_btn.clicked.connect(self._open_history)
        btn_row.addWidget(history_btn)

        help_btn = QPushButton("Help")
        help_btn.setMinimumSize(90, 44)
        help_btn.clicked.connect(self._show_help)
        btn_row.addWidget(help_btn)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── Status bar ────────────────────────────────────────────────────────
        sb = QStatusBar()
        sb.showMessage(f"{APP_NAME} v{APP_VERSION} — ready")
        self.setStatusBar(sb)
        self._status_bar = sb

    # ── Slot handlers ─────────────────────────────────────────────────────────

    def _on_action_changed(self, index: int) -> None:
        is_transcribe = (index == 1)
        self.model_label.setVisible(is_transcribe)
        self.model_combo.setVisible(is_transcribe)

    def _browse_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Audio / Video File", "", MEDIA_FILTER,
        )
        if path:
            self.source_edit.setText(path)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Output Folder", self.folder_edit.text()
        )
        if folder:
            self.folder_edit.setText(folder)

    # ── Queue ─────────────────────────────────────────────────────────────────

    def _add_to_queue(self) -> None:
        """Let the user pick one or more files to add to the queue."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add Files to Queue", "", MEDIA_FILTER,
        )
        for path in paths:
            if os.path.isfile(path):
                name = os.path.basename(path)
                self._queue.append((path, ""))   # no custom name override
                item = QListWidgetItem(f"  {name}")
                item.setToolTip(path)
                self.queue_list.addItem(item)
        self._refresh_queue_label()

    def _clear_queue(self) -> None:
        self._queue.clear()
        self.queue_list.clear()
        self._refresh_queue_label()

    def _refresh_queue_label(self) -> None:
        n = len(self._queue)
        self.queue_label.setText(f"Queue: {n} file{'s' if n != 1 else ''}")

    def _pop_queue_and_start(self) -> None:
        """Start the next item in the queue, if any."""
        if not self._queue:
            return
        source, custom_name = self._queue.pop(0)
        self.queue_list.takeItem(0)
        self._refresh_queue_label()
        self._launch_worker(source, custom_name)

    # ── Processing ────────────────────────────────────────────────────────────

    def _start(self) -> None:
        source = self.source_edit.text().strip()
        folder = self.folder_edit.text().strip()

        if not source:
            QMessageBox.warning(self, "Input Missing", "Please select a source file.")
            return
        if not os.path.isfile(source):
            QMessageBox.warning(self, "File Not Found", "The selected file does not exist.")
            return
        if not folder:
            QMessageBox.warning(self, "Output Missing", "Please choose an output folder.")
            return

        os.makedirs(folder, exist_ok=True)
        self._launch_worker(source, self.name_edit.text())

    def _launch_worker(self, source: str, custom_name: str) -> None:
        folder  = self.folder_edit.text().strip() or str(Path.home() / "Downloads")
        action  = "text" if self.action_combo.currentIndex() == 1 else "audio"

        self._worker = ProcessingThread(
            source        = source,
            output_folder = folder,
            action        = action,
            model_size    = self.model_combo.currentText(),
            custom_name   = custom_name,
        )
        self._worker.progress_signal.connect(self._on_progress)
        self._worker.status_signal.connect(self._on_status)
        self._worker.finished_signal.connect(self._on_finished)

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("Processing…")
        self._status_bar.showMessage(
            f"Processing: {os.path.basename(source)}"
            + (f"  |  Queue: {len(self._queue)}" if self._queue else "")
        )

        _prevent_sleep()
        self._worker.start()

    def _stop(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._queue.clear()
            self.queue_list.clear()
            self._refresh_queue_label()
            self.stop_btn.setEnabled(False)
            self.progress_label.setText("⛔  Stopping…")
            self._status_bar.showMessage("Stopping…")

    def _on_progress(self, value: float) -> None:
        self.progress_bar.setValue(int(value))
        self.progress_label.setText(f"Progress: {int(value)} %")

    def _on_status(self, msg: str) -> None:
        self.progress_label.setText(msg)
        self._status_bar.showMessage(msg)

    def _on_finished(self) -> None:
        _allow_sleep()

        if self._queue:
            # Automatically process the next queued file
            self._status_bar.showMessage(
                f"✅  Done — starting next file  ({len(self._queue)} remaining)"
            )
            self.progress_label.setText(
                f"✅  Done — starting next  ({len(self._queue)} remaining)"
            )
            self._pop_queue_and_start()
        else:
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.progress_bar.setValue(100)
            self.progress_label.setText("✅  All done")
            self._status_bar.showMessage("✅  All done")

        if self._history_win and self._history_win.isVisible():
            self._history_win.load_history()

    # ── Other handlers ────────────────────────────────────────────────────────

    def _open_history(self) -> None:
        if self._history_win is None or not self._history_win.isVisible():
            self._history_win = HistoryWindow()
        self._history_win.show()
        self._history_win.raise_()
        self._history_win.load_history()

    def _on_update(self, version: str) -> None:
        self._status_bar.showMessage(f"Update available: v{version}")
        msg = QMessageBox(self)
        msg.setWindowTitle("Update Available")
        msg.setText(
            f"<h3>New version available!</h3>"
            f"<p>Your version: <b>v{APP_VERSION}</b><br>"
            f"Latest:&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; <b>v{version}</b></p>"
            f"<p>Download from GitHub?</p>"
        )
        msg.setStyleSheet("background-color:#161b22; color:#c9d1d9;")
        dl_btn = msg.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() == dl_btn:
            webbrowser.open(f"https://github.com/{GITHUB_REPO}/releases/latest")

    def _show_help(self) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle(f"{APP_NAME} — Help")
        msg.setText(
            "<h2>How to Use</h2>"
            "<p><b>1. Source File</b> — browse or drag &amp; drop an audio "
            "or video file (MP3, WAV, M4A, FLAC, OGG, MP4, MKV, MOV, etc.)</p>"
            "<p><b>2. Output Folder</b> — where the processed file is saved.</p>"
            "<p><b>3. Custom Filename (optional)</b> — leave blank to keep the "
            "original name.</p>"
            "<p><b>4. Action</b></p>"
            "<ul>"
            "<li><b>Copy audio only</b> — copies the file to the output folder.</li>"
            "<li><b>Transcribe to text</b> — copies the file AND generates a "
            "timestamped transcript with Whisper AI.</li>"
            "</ul>"
            "<p><b>5. Whisper Model</b> (only when transcribing)</p>"
            "<table border='1' cellpadding='5' cellspacing='0' "
            "style='border-collapse:collapse;'>"
            "<tr style='background:#1f6feb;color:white;'>"
            "<th>Model</th><th>Speed</th><th>Accuracy</th></tr>"
            "<tr><td>tiny</td><td>⚡⚡⚡⚡</td><td>⭐</td></tr>"
            "<tr><td>base</td><td>⚡⚡⚡</td><td>⭐⭐</td></tr>"
            "<tr><td>small</td><td>⚡⚡</td><td>⭐⭐⭐ (default)</td></tr>"
            "<tr><td>medium</td><td>⚡</td><td>⭐⭐⭐⭐</td></tr>"
            "<tr><td>large-v3</td><td>🐢</td><td>⭐⭐⭐⭐⭐</td></tr>"
            "</table>"
            "<p>Models download automatically on first use and are cached locally.</p>"
            "<p><b>6. Queue</b> — click <b>➕ Add File to Queue</b> at any time "
            "(even while processing) to line up more files. They run automatically "
            "one after another.</p>"
            "<p><b>7. Sleep prevention</b> — while processing is active the app "
            "tells Windows / macOS / Linux not to sleep, so long jobs finish even "
            "if you walk away.</p>"
            "<p><b>8. History</b> — play back, export, or delete past entries.</p>"
        )
        msg.setStyleSheet("background-color:#161b22; color:#c9d1d9;")
        msg.exec()


# ══════════════════════════════════════════════════════════════════════════════
#  Stylesheet
# ══════════════════════════════════════════════════════════════════════════════

_DARK_CSS = """
QWidget {
    background-color: #0d1117;
    color: #c9d1d9;
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 10pt;
}
QMainWindow  { background-color: #0d1117; }
QDialog      { background-color: #0d1117; }
QGroupBox {
    border: 2px solid #30363d;
    border-radius: 8px;
    margin-top: 12px;
    padding: 10px 8px 8px 8px;
    font-weight: bold;
    color: #8b949e;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: #58a6ff;
}
QLineEdit {
    background-color: #161b22;
    border: 2px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    color: #c9d1d9;
    selection-background-color: #1f6feb;
}
QLineEdit:focus { border: 2px solid #58a6ff; }
QListWidget {
    background-color: #161b22;
    border: 2px solid #30363d;
    border-radius: 6px;
    color: #c9d1d9;
    padding: 4px;
}
QListWidget::item:selected { background-color: #1f6feb; }
QComboBox {
    background-color: #161b22;
    border: 2px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    color: #c9d1d9;
}
QComboBox:hover { border: 2px solid #58a6ff; }
QComboBox QAbstractItemView {
    background-color: #161b22;
    color: #c9d1d9;
    selection-background-color: #1f6feb;
    border: 1px solid #30363d;
}
QPushButton {
    background-color: #238636;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: bold;
}
QPushButton:hover    { background-color: #2ea043; }
QPushButton:pressed  { background-color: #1a7f37; }
QPushButton:disabled { background-color: #21262d; color: #6e7681; }
QProgressBar {
    background-color: #161b22;
    border: 2px solid #30363d;
    border-radius: 6px;
    text-align: center;
    color: #c9d1d9;
    height: 22px;
}
QProgressBar::chunk {
    background-color: #1f6feb;
    border-radius: 5px;
}
QStatusBar  { background-color: #161b22; color: #8b949e; }
QLabel      { color: #c9d1d9; }
QMessageBox { background-color: #0d1117; }
"""


# ══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    _init_db()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()